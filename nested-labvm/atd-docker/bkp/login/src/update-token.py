#!/usr/bin/env python

#Save running config on all switches

import jsonrpclib,ssl,sys,subprocess,paramiko,jsonrpclib,requests,json
from paramiko import SSHClient
import yaml
from scp import SCPClient
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success

# Initialize cloud logging
logger = setup_cloud_logging('update-token')

#static files
labACCESS = '/etc/atd/ACCESS_INFO.yaml'



try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def createSSHClient(server, port, user, password):
    client = SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(server, port, user, password)
    return client


def saveUploadKey(allHosts,labPassword):
    local_file = 'cv-onboarding-token'
    remote_path = '/mnt/flash/'
    user = 'arista'
    port = 22  # Default SSH port
    log_operation_start(logger, 'update-token', host_count=len(allHosts))
    successful_updates = 0
    failed_updates = 0

    for IPaddress in allHosts:
        logger.info(f"Updating token on {IPaddress}", extra={'labels': {'ip': IPaddress, 'operation': 'update-token'}})
        try:
            #use eAPI to copy the running-config to start-config
            ssh = createSSHClient(IPaddress, port, user, labPassword)
            with SCPClient(ssh.get_transport()) as scp:
                scp.put(local_file, remote_path + local_file)
                print(f"File copied to {IPaddress}")
                logger.info(f"Token file copied to {IPaddress}", extra={'labels': {'ip': IPaddress, 'status': 'copied'}})
        except Exception as e:
            error_msg = f"Failed to copy file to {IPaddress}: {e}"
            print(error_msg)
            logger.error(error_msg, extra={'labels': {'ip': IPaddress, 'error': str(e)}})

        except KeyboardInterrupt:
            print("Caught Keyboard Interrupt - Exiting")
            logger.warning("Token update interrupted by user")
            sys.exit()

        except OSError as ERR :
            # Socket Errors
            print(ERR)
            logger.error(f"Socket error on {IPaddress}: {ERR}", extra={'labels': {'ip': IPaddress, 'error': str(ERR)}})

        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = IPaddress))
        try:
            config = switch.runCmds(1,["enable", "configure","aaa authentication login default group atds local","ip host apiserver.arista.io 35.192.157.156","ip host arista.io 34.67.65.165","ip host www.arista.io 34.67.65.165","ip host www.cv-staging.corp.arista.io 34.82.61.12","ip name-server vrf MGMT 8.8.8.8","daemon TerminAttr","exec /usr/bin/TerminAttr -smashexcludes=ale,flexCounter,hardware,kni,pulse,strata -cvaddr=apiserver.arista.io:443 -cvauth=token-secure,/mnt/flash/cv-onboarding-token -cvvrf=MGMT -taillogs","no shutdown","no aaa authorization exec default group atds local","no aaa authorization commands all default local","write memory"],"text")
            runConfig = (config[1]["output"])
            logger.info(f"Token configuration applied on {IPaddress}", extra={'labels': {'ip': IPaddress, 'status': 'configured'}})
            successful_updates += 1
        except Exception as e:
            error_msg = str(e)
            print(error_msg)
            print(f"Check eAPI is enabled on {IPaddress}")
            logger.error(f"Failed to configure token on {IPaddress}: {error_msg}", extra={'labels': {'ip': IPaddress, 'error': error_msg}})
            failed_updates += 1

    log_operation_success(logger, 'update-token', successful=successful_updates, failed=failed_updates)



def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology']



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
       topology = yaml.load(f,Loader=yaml.FullLoader)
    hosts = []
    for a in topology['nodes']:
        for key in a.keys():
            hosts.append(a[key]['ip_addr'])
    return hosts

def getkey():
    key = input("Please enter the token: ")

    # Open the file in write mode
    with open("cv-onboarding-token", "w") as file:
        # Write the key to the file
        file.write(key)

def cvpAuth(labPassword):
    headers = { 'Content-Type': 'application/json' }
    loginURL = "/web/login/authenticate.do"
    authenticateData = json.dumps({'userId' : cvpUser, 'password' : labPassword})
    response = requests.post(url+loginURL,data=authenticateData,headers=headers,verify=False)
    assert response.ok
    cookies = response.cookies
    return cookies



def main():
    logger.info("Starting token update process")
    labPassword, labTopology = readLabDetails()
    logger.info(f"Loaded lab topology: {labTopology}", extra={'labels': {'topology': labTopology}})

    allHosts = readAtdTopo(labTopology)
    logger.info(f"Found {len(allHosts)} hosts to update", extra={'labels': {'host_count': str(len(allHosts))}})

    getkey()
    logger.info("Token received from user")

    saveUploadKey(allHosts,labPassword)
    logger.info("Token update process completed")

main()