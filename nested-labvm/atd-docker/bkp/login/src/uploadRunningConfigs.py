#!/usr/bin/env python

# This script saves the running config of each switch to a local folder

import jsonrpclib
import yaml
import ssl
import os

import json
import requests

from google.cloud import storage
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error

# Initialize cloud logging
logger = setup_cloud_logging('uploadRunningConfigs')

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

    lab_name = labDetails.get('name', 'unknown')
    log_operation_start(logger, 'upload-configs', lab_name=lab_name, host_count=len(allHostsName))
    logger.info("Starting uploadSwitchDetails function", extra={'labels': {'hosts': str(allHostsName[:5])}})
    logger.info(f"Hosts to process: {len(allHostsName)}")

    try:
        #Bucket and details
        storage_client = storage.Client()
        bucket_name = labDetails["project"]+"-grading"
        bucket_folder_path = labDetails["name"]
        logger.info(f"Bucket name: {bucket_name}", extra={'labels': {'bucket': bucket_name, 'path': bucket_folder_path}})
        logger.info(f"Bucket folder path: {bucket_folder_path}")

        bucket_obj = storage_client.bucket(bucket_name)

        # Verify bucket exists
        if not bucket_obj.exists():
            logger.error(f"Bucket {bucket_name} does not exist!", extra={'labels': {'bucket': bucket_name, 'error': 'bucket_not_found'}})
            raise Exception(f"Bucket {bucket_name} not found")

        logger.info(f"Successfully connected to bucket: {bucket_name}", extra={'labels': {'bucket': bucket_name, 'status': 'connected'}})

        #Upload grading config
        grading_config_path = f"{bucket_folder_path}/grading_config.yaml"
        if not bucket_obj.blob(grading_config_path).exists():
            logger.info("Grading config does not exist, creating new one", extra={'labels': {'path': grading_config_path}})
            try:
                grading_config = yaml.dump({"to_be_graded": allHostsName, "templates": labDetails["labguides_modules"]}, default_flow_style=False)
                blob_obj = bucket_obj.blob(grading_config_path)
                blob_obj.upload_from_string(grading_config, content_type="yaml")
                logger.info(f"Successfully uploaded grading config to {grading_config_path}", extra={'labels': {'path': grading_config_path, 'status': 'uploaded'}})

                # Upload tar file
                tar_filename = f"{labDetails['name'][:-11]}.gz"
                tar_local_path = f"/home/arista/{tar_filename}"
                tar_remote_path = f"{bucket_folder_path}/{tar_filename}"

                if os.path.exists(tar_local_path):
                    blob_obj_tar = bucket_obj.blob(tar_remote_path)
                    blob_obj_tar.upload_from_filename(tar_local_path)
                    logger.info(f"Successfully uploaded tar file from {tar_local_path} to {tar_remote_path}", extra={'labels': {'file': tar_filename, 'status': 'uploaded'}})
                else:
                    logger.warning(f"Tar file not found at {tar_local_path}, skipping tar upload", extra={'labels': {'file': tar_filename, 'status': 'skipped'}})

            except Exception as e:
                logger.error(f"Failed to upload grading config or tar file: {str(e)}", extra={'labels': {'error': str(e)}})
                raise
        else:
            logger.info(f"Grading config already exists at {grading_config_path}, skipping upload", extra={'labels': {'path': grading_config_path, 'status': 'exists'}})

        #get running configs
        successful_uploads = 0
        failed_uploads = 0

        for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
            logger.info(f"Processing device: {name} ({ip})", extra={'labels': {'device': name, 'ip': ip}})

            try:
                switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(
                    password = labDetails['login_info']['jump_host']['pw'],
                    ipaddress = ip
                ))
                logger.info(f"Connected to switch {name} at {ip}", extra={'labels': {'device': name, 'status': 'connected'}})

                config = switch.runCmds(1,["enable", "show running-config"],"text")
                runConfig = (config[1]["output"])
                logger.info(f"Retrieved running config from {name} (length: {len(runConfig)} characters)", extra={'labels': {'device': name, 'config_size': str(len(runConfig))}})

                filename = str(name) + "-running" + ".txt"
                remote_path = f"{bucket_folder_path}/running-configs/{filename}"
                blob_obj = bucket_obj.blob(remote_path)

                blob_obj.upload_from_string(runConfig, content_type="text/plain")
                logger.info(f"Successfully uploaded {filename} to {remote_path}", extra={'labels': {'device': name, 'file': filename, 'status': 'uploaded'}})
                successful_uploads += 1

            except jsonrpclib.ProtocolError as e:
                logger.error(f"JSONRPC Protocol error for {name} ({ip}): {str(e)}", extra={'labels': {'device': name, 'error_type': 'protocol_error'}})
                failed_uploads += 1
            except Exception as e:
                logger.error(f"Failed to process device {name} ({ip}): {str(e)}", extra={'labels': {'device': name, 'error': str(e)}})
                failed_uploads += 1

        logger.info(f"Upload summary - Successful: {successful_uploads}, Failed: {failed_uploads}", extra={'labels': {'successful': str(successful_uploads), 'failed': str(failed_uploads)}})
        log_operation_success(logger, 'upload-configs', lab_name=lab_name, successful=successful_uploads, failed=failed_uploads)

    except Exception as e:
        logger.error(f"Critical error in uploadSwitchDetails: {str(e)}", extra={'labels': {'error': str(e)}})
        log_operation_error(logger, 'upload-configs', str(e), lab_name=lab_name)
        raise




def main():
    try:
        logger.info("="*60)
        logger.info("Starting main function for uploadRunningConfigs")

        labDetails = readYaml(labACCESS)
        lab_name = labDetails.get('name', 'Unknown')
        logger.info(f"Lab details loaded: {lab_name}", extra={'labels': {'lab_name': lab_name}})
        logger.info(f"Topology: {labDetails.get('topology', 'Unknown')}", extra={'labels': {'topology': labDetails.get('topology', 'Unknown')}})

        allHostsIP, allHostsName = readAtdTopo(labDetails["topology"])
        logger.info(f"Topology loaded - Found {len(allHostsName)} hosts", extra={'labels': {'host_count': str(len(allHostsName))}})

        restarted = 0
        uploadSwitchDetails(allHostsName,allHostsIP,labDetails)

        logger.info("Successfully completed all uploads", extra={'labels': {'lab_name': lab_name, 'status': 'complete'}})
        print("Successfully Uploaded")

    except FileNotFoundError as e:
        logger.error(f"File not found: {str(e)}", extra={'labels': {'error_type': 'file_not_found', 'error': str(e)}})
        print(f"ERROR: File not found - {str(e)}")
    except KeyError as e:
        logger.error(f"Missing key in configuration: {str(e)}", extra={'labels': {'error_type': 'missing_key', 'error': str(e)}})
        print(f"ERROR: Missing configuration key - {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in main: {str(e)}", extra={'labels': {'error': str(e)}})
        print(f"ERROR: Upload failed - {str(e)}")
        raise


if __name__ == "__main__":
    main()