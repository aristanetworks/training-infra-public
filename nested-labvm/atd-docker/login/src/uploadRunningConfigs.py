#!/usr/bin/env python

# This script saves the running config of each switch to a local folder

import jsonrpclib
import yaml
import ssl
import os
import logging

import json
import requests

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/arista/uploadRunningConfigs.log'),
        logging.StreamHandler()
    ]
)

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

requests.packages.urllib3.disable_warnings()

labACCESS = '/etc/atd/ACCESS_INFO.yaml'
regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
cvpHost = "192.168.0.5"
cvpUser = "arista"
url = "https://{host}".format(host=cvpHost)


def readYaml(file=labACCESS):
    # get the lab password and the topolgy in use
    with open(file) as f:
        yaml_data = yaml.load(f,Loader=yaml.FullLoader)
    return yaml_data



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
        topology = yaml.load(f,Loader=yaml.FullLoader)
        mylist= topology['nodes']
        test=[]
        for item in mylist:
           test.append(list(item.keys()))
           hostsName = [item for sublist in test for item in sublist]
    hostsIP = []
    for a in topology['nodes']:
        for key in a.keys():
            hostsIP.append(a[key]['ip_addr'])
    return hostsIP, hostsName



def uploadSwitchDetails(allHostsName,allHostsIP,labDetails):
    pingDone = 0
    evpnNOTdone = 0

    logging.info("Starting uploadSwitchDetails function")
    logging.info(f"Hosts to process: {allHostsName}")
    logging.info(f"Host IPs: {allHostsIP}")

    try:
        #Bucket and details
        storage_client = storage.Client()
        bucket_name = labDetails["project"]+"-grading"
        bucket_folder_path = labDetails["name"]
        logging.info(f"Bucket name: {bucket_name}")
        logging.info(f"Bucket folder path: {bucket_folder_path}")

        bucket_obj = storage_client.bucket(bucket_name)

        # Verify bucket exists
        if not bucket_obj.exists():
            logging.error(f"Bucket {bucket_name} does not exist!")
            raise Exception(f"Bucket {bucket_name} not found")

        logging.info(f"Successfully connected to bucket: {bucket_name}")

        #Upload grading config
        grading_config_path = f"{bucket_folder_path}/grading_config.yaml"
        if not bucket_obj.blob(grading_config_path).exists():
            logging.info("Grading config does not exist, creating new one")
            try:
                grading_config = yaml.dump({"to_be_graded": allHostsName, "templates": labDetails["labguides_modules"]}, default_flow_style=False)
                blob_obj = bucket_obj.blob(grading_config_path)
                blob_obj.upload_from_string(grading_config, content_type="yaml")
                logging.info(f"Successfully uploaded grading config to {grading_config_path}")

                # Upload tar file
                tar_filename = f"{labDetails['name'][:-11]}.gz"
                tar_local_path = f"/home/arista/{tar_filename}"
                tar_remote_path = f"{bucket_folder_path}/{tar_filename}"

                if os.path.exists(tar_local_path):
                    blob_obj_tar = bucket_obj.blob(tar_remote_path)
                    blob_obj_tar.upload_from_filename(tar_local_path)
                    logging.info(f"Successfully uploaded tar file from {tar_local_path} to {tar_remote_path}")
                else:
                    logging.warning(f"Tar file not found at {tar_local_path}, skipping tar upload")

            except Exception as e:
                logging.error(f"Failed to upload grading config or tar file: {str(e)}", exc_info=True)
                raise
        else:
            logging.info(f"Grading config already exists at {grading_config_path}, skipping upload")

        #get running configs
        successful_uploads = 0
        failed_uploads = 0

        for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
            logging.info(f"Processing device: {name} ({ip})")

            try:
                switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(
                    password = labDetails['login_info']['jump_host']['pw'],
                    ipaddress = ip
                ))
                logging.info(f"Connected to switch {name} at {ip}")

                config = switch.runCmds(1,["enable", "show running-config"],"text")
                runConfig = (config[1]["output"])
                logging.info(f"Retrieved running config from {name} (length: {len(runConfig)} characters)")

                filename = str(name) + "-running" + ".txt"
                remote_path = f"{bucket_folder_path}/running-configs/{filename}"
                blob_obj = bucket_obj.blob(remote_path)

                blob_obj.upload_from_string(runConfig, content_type="text/plain")
                logging.info(f"Successfully uploaded {filename} to {remote_path}")
                successful_uploads += 1

            except jsonrpclib.ProtocolError as e:
                logging.error(f"JSONRPC Protocol error for {name} ({ip}): {str(e)}", exc_info=True)
                failed_uploads += 1
            except Exception as e:
                logging.error(f"Failed to process device {name} ({ip}): {str(e)}", exc_info=True)
                failed_uploads += 1

        logging.info(f"Upload summary - Successful: {successful_uploads}, Failed: {failed_uploads}")

    except Exception as e:
        logging.error(f"Critical error in uploadSwitchDetails: {str(e)}", exc_info=True)
        raise




def main():
    try:
        logging.info("="*60)
        logging.info("Starting main function")

        labDetails = readYaml(labACCESS)
        logging.info(f"Lab details loaded: {labDetails.get('name', 'Unknown')}")
        logging.info(f"Topology: {labDetails.get('topology', 'Unknown')}")

        allHostsIP, allHostsName = readAtdTopo(labDetails["topology"])
        logging.info(f"Topology loaded - Found {len(allHostsName)} hosts")

        restarted = 0
        uploadSwitchDetails(allHostsName,allHostsIP,labDetails)

        logging.info("Successfully completed all uploads")
        print("Successfully Uploaded")

    except FileNotFoundError as e:
        logging.error(f"File not found: {str(e)}", exc_info=True)
        print(f"ERROR: File not found - {str(e)}")
    except KeyError as e:
        logging.error(f"Missing key in configuration: {str(e)}", exc_info=True)
        print(f"ERROR: Missing configuration key - {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error in main: {str(e)}", exc_info=True)
        print(f"ERROR: Upload failed - {str(e)}")
        raise


if __name__ == "__main__":
    main()