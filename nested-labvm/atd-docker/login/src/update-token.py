#!/usr/bin/env python

#Save running config on all switches

import jsonrpclib,ssl,sys,subprocess,paramiko,jsonrpclib,requests,json
from paramiko import SSHClient
import yaml
from scp import SCPClient

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
    for IPaddress in allHosts:
        try:
            #use eAPI to copy the running-config to start-config
            ssh = createSSHClient(IPaddress, port, user, labPassword)
            with SCPClient(ssh.get_transport()) as scp:
                scp.put(local_file, remote_path + local_file)
                print(f"File copied to {IPaddress}")
        except Exception as e:
            print(f"Failed to copy file to {IPaddress}: {e}")

        except KeyboardInterrupt:
            print("Caught Keyboard Interrupt - Exiting")
            sys.exit()

        except OSError as ERR :
            # Socket Errors
            print(ERR)
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = IPaddress))
        try:
            config = switch.runCmds(1,["enable", "configure","aaa authentication login default group atds local","ip host apiserver.arista.io 35.192.157.156","ip host arista.io 34.67.65.165","ip host www.arista.io 34.67.65.165","ip host www.cv-staging.corp.arista.io 34.82.61.12","ip name-server vrf MGMT 8.8.8.8","daemon TerminAttr","exec /usr/bin/TerminAttr -smashexcludes=ale,flexCounter,hardware,kni,pulse,strata -cvaddr=apiserver.arista.io:443 -cvauth=token-secure,/mnt/flash/cv-onboarding-token -cvvrf=MGMT -taillogs","no shutdown","no aaa authorization exec default group atds local","no aaa authorization commands all default local","write memory"],"text")
            runConfig = (config[1]["output"])
        except Exception as e:
            print(str(e))
            print("Check eAPI is enabled on {switch}".format(switch = name))



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
    labPassword, labTopology = readLabDetails()
    allHosts = readAtdTopo(labTopology)
    getkey()
    saveUploadKey(allHosts,labPassword)

main()