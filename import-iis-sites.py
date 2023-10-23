#!/usr/bin/env python3

"""
This script imports websites from an IIS instance and generate Icinga 2
services

authors:
    Mattia Martinello - mattia@mattiamartinello.com
"""

_VERSION = '1.0'
_VERSION_DESCR = 'Icinga 2 IIS Sites Importer'
_COMMAND = 'Get-IISSite | ft Name,State,Bindings -HideTableHeaders -auto'

import argparse
import jinja2
import logging
import re
import subprocess
import winrm
import yaml


class Importer:
    """Connect to the Windows host, import IIS sites and write the Icinga 2
    configuration file.
    """

    def __init__(self):
        # init the cmd line parser
        parser = argparse.ArgumentParser(
            description='Icinga 2 IIS Sites Importer'
        )
        self._add_arguments(parser)

        # read the command line
        args = parser.parse_args()

        # manage arguments
        self._manage_arguments(args)

        # Load and parse the configuration file if provided
        if self.conf_file is not None:
            conf = self._load_conf_file()
            self._parse_conf(conf)


    def _add_arguments(self, parser):
        """Add command arguments to the argument parser.
        """

        parser.add_argument(
            '-V', '--version',
            action='version',
            version = '%(prog)s v{} - {}'.format(_VERSION, _VERSION_DESCR)
        )

        parser.add_argument(
            '--debug',
            action="store_true",
            help='Print debugging info to console '
                 '(WARNING: password will be printed!)'
        )

        parser.add_argument(
            '-u', '--url',
            dest='url',
            default='http://localhost:5985/wsman',
            help='The Windows winrm host'
        )

        parser.add_argument(
            '-U', '--username',
            dest='username',
            help='The Windows winrm username'
        )

        parser.add_argument(
            '-p', '--password',
            dest='password',
            help='The Windows winrm password'
        )

        parser.add_argument(
            '-k', '--insecure',
            action='store_true',
            help='Skip the SSL certificate verification'
        )

        parser.add_argument(
            '-o', '--output-file',
            dest='output_file',
            help='The Icinga 2 output file'
        )

        parser.add_argument(
            '-r', '--reload',
            action='store_true',
            help='Reload Icinga 2 after import'
        )

        parser.add_argument(
            '-t', '--template-file',
            dest='template_file',
            help='The Jinja template file to be used to generate the output file'
        )

        parser.add_argument(
            '-c', '--conf-file',
            dest='conf_file',
            help='The path of configuration file'
        )


    def _manage_arguments(self, args):
        """Get command arguments from the argument parser and load them.
        """

        # Debug flag
        self.debug = getattr(args, 'debug', False)
        if self.debug:
            logging.basicConfig(level=logging.DEBUG)

        # WinRM url, username and password
        self.winrm_url = getattr(args, 'url')
        self.winrm_username = getattr(args, 'username')
        self.winrm_password = getattr(args, 'password')

        # Insecure flag: skip SSL certificate validation
        self.winrm_insecure = getattr(args, 'insecure', False)

        # Output file: the output file will be saved here
        self.output_file_path = getattr(args, 'output_file')

        # Template file: the output file will be saved here
        self.template_file_path = getattr(args, 'template_file')

        # Reload flag: reload Icinga 2 at the end
        self.icinga2_reload = getattr(args, 'reload', False)

        # Configuration file
        self.conf_file = getattr(args, 'conf_file', None)

        # Print arguments (debug)
        logging.debug('Command arguments: {}'.format(args))


    def _load_conf_file(self):
        # If configuration file is not set return None
        conf_file_path = self.conf_file
        if conf_file_path is None:
            return None

        logging.info("Loading settings from {} ...".format(conf_file_path))

        # Load the configuration file
        with open(conf_file_path) as f:
            conf = yaml.load(f, Loader=yaml.loader.SafeLoader)

        # Return the configuration
        logging.debug("Loaded configuration: {}".format(conf))
        return conf


    def _parse_conf(self, conf={}):
        if conf['winrm_url']:
            self.winrm_url = conf['winrm_url']

        if conf['winrm_username']:
            self.winrm_username = conf['winrm_username']

        if conf['winrm_password']:
            self.winrm_password = conf['winrm_password']

        if conf['winrm_insecure']:
            self.winrm_insecure = conf['winrm_insecure']

        if conf['template_file']:
            self.template_file_path = conf['template_file']

        if conf['output_file']:
            self.output_file_path = conf['output_file']

        if conf['reload_icinga']:
            self.reload_icinga = conf['reload_icinga']

        if conf['site_attributes']:
            self.site_attributes = conf['site_attributes']
        else:
            self.site_attributes = {}


    def _winrm_connect(self, url, username, password, insecure=False):
        if insecure:
            server_cert_validation = 'ignore'
        else:
            server_cert_validation = 'validate'

        session = winrm.Session(
            url,
            auth=(username, password),
            transport='ntlm',
            server_cert_validation=server_cert_validation
        )

        return session


    def _execute_ps(self, session, command):
        rs = session.run_ps(command)
        self.std_out = rs.std_out
        self.std_err = rs.std_err
        self.status_code = rs.status_code

    def _parse_iis_sites(self, input):
        input = input.decode('utf-8')
        sites = []

        rows = re.split('\\r\\n', input)
        for row in rows:
            # Skip empty rows
            if row == '':
                continue
            
            # Trim the line
            row = row.strip()

            # Match the line exporting data from the table
            logging.debug("Exporting the row '{}'".format(row))
            try:
                pattern = r'^(\S+)\s+(.+)\s+{(.+)}\s*$'
                matches = re.search(pattern, row)
                name = matches.group(1)
                state = matches.group(2)
                bindings = matches.group(3)

                logging.debug("Name: {}".format(name))
                logging.debug("State: {}".format(state))
                logging.debug("Bindings: {}".format(bindings))

                bindings = self._parse_bindings(bindings)

                site = {
                    'name': name,
                    'state': state,
                    'bindings': bindings
                }
                sites.append(site)
            except:
                continue

        return sites


    def _parse_bindings(self, input):
        # http *:80:romacostruzioni-alcamo-dbw.test.ies.it,
        bindings = []

        rows = re.split(',', input)
        for row in rows:
            logging.debug("Matching the binding '{}'".format(row))
            try:
                pattern = r'^(\S+)\s(.+):([0-9]+):(\S+)$'
                matches = re.search(pattern, row)
                type = matches.group(1)
                ip_address = matches.group(2)
                port = matches.group(3)
                host_name = matches.group(4)

                logging.debug("Type: {}".format(type))
                logging.debug("IP Address: {}".format(ip_address))
                logging.debug("Port: {}".format(port))
                logging.debug("Host name: {}".format(host_name))

                binding = {
                    'type': type,
                    'ip_address': ip_address,
                    'port': port,
                    'host_name': host_name
                }
                bindings.append(binding)
            except:
                continue

        return bindings


    def _compile_sites_objects(self, sites, site_attributes={}):
        objects = []

        logging.debug("Passed site attributes: {}".format(site_attributes))

        # Cycle sites
        for site in sites:
            site_name = site['name']
            logging.debug("Site name: {}".format(site_name))

            # If attributes are set for the current site, add them, else
            # add nothing
            if site_name in site_attributes:
                attributes = site_attributes[site_name]
            else:
                attributes = {}

            # Add the attribute property to the current site object
            site['attributes'] = attributes

            # Add the current site to site objects
            objects.append(site)

        # Returb the compiled site objects
        return objects


    def _get_attributes(self):
        return self.site_attributes

    
    def _write_output_file(self, template_file, output_file, sites):
        template_loader = jinja2.FileSystemLoader(searchpath="./")
        template_env = jinja2.Environment(loader=template_loader)
        template = template_env.get_template(template_file)

        # Build file content
        output = template.render(
            sites=sites
        )

        # Write output file
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output)
            f.close

            logging.info("File {} written!".format(output_file))
        except:
            logging.error("Error writing file {}!".format(output_file))


    def _reload_icinga(self):
        command = ["systemctl", "reload", "icinga2"]
        logging.info("Reloading Icinga2 ...")
        logging.debug("Reload command: {}".format(command))
        
        try:
            reload_process = subprocess.run(command)
            return_code = reload_process.returncode

            if return_code == 0:
                logging.debug("Icinga2 succesfully reloaded!")
            else:
                msg = "Error during Icinga2 reload, command exited with {}!"
                msg = msg.format(return_code)
                logging.warning(msg)

                msg = "Reload command stdout: {}".format(reload_process.stdout)
                logging.debug(msg)

                msg = "Reload command stderr: {}".format(reload_process.stderr)
                logging.debug(msg)
        except Exception as error:
            msg = "Cannot reload Icinga2 due to unexpected error: {}!"
            msg = msg.format(error)
            logging.error(msg)


    def handle(self):
        msg = "Creating a new WinRM connection to {} with username {}"
        msg+= " and password {} ..."
        msg = msg.format(
            self.winrm_url,
            self.winrm_username,
            self.winrm_password
        )
        logging.debug(msg)

        # Execute a WinRM connection
        session = self._winrm_connect(
            self.winrm_url,
            self.winrm_username,
            self.winrm_password,
            self.winrm_insecure
        )

        # Execute the PowerShell command
        command = _COMMAND
        msg = "Executing the PowerShell command: {} ...".format(command)
        logging.debug(msg)
        self._execute_ps(session, command)

        std_out = self.std_out
        logging.debug("Command output: {}".format(std_out))
        iis_sites = self._parse_iis_sites(std_out)
        logging.debug("IIS Sites: {}".format(iis_sites))

        # Get attributes
        site_attributes = self._get_attributes()
        logging.debug("Site attributes: {}".format(site_attributes))

        # Combine the attributes with sites to generate site objects
        sites = self._compile_sites_objects(iis_sites, site_attributes)
        logging.debug("Sites objects: {}".format(sites))

        # Write the output file
        self._write_output_file(self.template_file_path, 
                                self.output_file_path,
                                sites)
        
        # Reload Icinga2 if needed
        if self.icinga2_reload:
            self._reload_icinga()
        else:
            logging.debug("Icinga2 reload not needed, skipping ...")


if __name__ == "__main__":
    # Run the program
    main = Importer()
    main.handle()
