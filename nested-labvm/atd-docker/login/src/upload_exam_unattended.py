#!/usr/bin/env python

# This script will export the running configs of each switch.  Make sure eAPI is configured correctly

import time
import jsonrpclib
import yaml
import ssl
import sys,os,re
import tarfile
from ftplib import FTP
import json
from base64 import b64decode, b64encode
import requests


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
EDIT_INSTANCE = 'https://us-central1-atd-testdrivetraining-prod.cloudfunctions.net/edit-instance'

def encodeID(tmp_data):
    tmp_str = json.dumps(tmp_data).encode()
    enc_str = b64encode(tmp_str).decode()
    return(enc_str)

def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology'],labDetails['name'],labDetails['zone'], labDetails['project']



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

def checkEmail(email):
    if(re.fullmatch(regex, email)):
        return 1
    else:
        print("Invalid Email")
        return 0


def ftpUpload(filename):
    try:
        with FTP('ftp.sdn-pros.com', 'ftpupload', 'ftpuser') as ftp, open(str(filename), 'rb') as file:
            ftp.cwd('/files/')
            if filename in ftp.nlst():
                print("It looks like you have already uploaded your exam files. If you think this is an error please contact SDN Pros - exams@sdn-pros.com")
            else:
                ftp.storbinary('STOR ' + str(filename), file)
    except:
        raise


def cvpAuth(labPassword):
    headers = { 'Content-Type': 'application/json' }
    loginURL = "/web/login/authenticate.do"
    authenticateData = json.dumps({'userId' : cvpUser, 'password' : labPassword})
    response = requests.post(url+loginURL,data=authenticateData,headers=headers,verify=False)
    assert response.ok
    cookies = response.cookies
    return cookies


def getConfiglets(cookies,localFolder):
    getConfigletURL = "/cvpservice/configlet/getConfiglets.do?"
    getConfigletParams = {'startIndex':'0','endIndex':'0'}
    response = requests.get(url+getConfigletURL,cookies=cookies, params=getConfigletParams,verify=False)
    outputConfiglets = response.json()
    localFilename = "allConfiglets"
    completePath = os.path.join(localFolder, localFilename)
    with open(completePath, 'w') as f:
        f.write(str(json.dump(outputConfiglets, f, indent=4)))
    return outputConfiglets

def getConfigletApplied(configletName,cookies):
    getConfigletURL = "/cvpservice/configlet/getAppliedDevices.do?"
    getConfigletParams = {'startIndex':'0','endIndex':'0','configletName':configletName}
    response = requests.get(url+getConfigletURL,cookies=cookies, params=getConfigletParams,verify=False)
    assert response.ok
    outputConfiglets = response.json()
    return outputConfiglets

def grabCVPInfo(labPassword,folder):
    print("Getting CVP details, this may take a few seconds")
    cookies = cvpAuth(labPassword)
    outputConfiglets = getConfiglets(cookies,folder)
    for item in outputConfiglets['data']:
        appliedConfiglets = getConfigletApplied(item['name'],cookies)
        if appliedConfiglets['total'] != 0:
            completePath = os.path.join(folder, item['name'])
            with open(completePath +".configlet", 'w') as f:
                f.write(str(json.dump(appliedConfiglets, f, indent=4)))
    print("CVP details saved")


def grabSwitchDetails(allHostsName, allHostsIP, folder, labPassword):
    """Collect and save configuration and operational data from Arista switches."""
    
    for name, ip in zip(reversed(allHostsName), reversed(allHostsIP)):
        print(f"Processing switch {name} at {ip}...")
        
        # Connect to the switch
        try:
            switch = jsonrpclib.Server(f"https://arista:{labPassword}@{ip}/command-api")
        except Exception as e:
            print(f"Failed to connect to {name}: {str(e)}")
            continue
        
        # Dictionary to store all outputs
        outputs = {}
        
        # Collect data with error handling for each command group
        collect_command_output(switch, name, outputs, "running", ["show running-config"])
        collect_mlag_info(switch, name, outputs)
        collect_vxlan_info(switch, name, outputs)
        collect_command_output(switch, name, outputs, "Route", ["show ip route vrf all"])
        collect_command_output(switch, name, outputs, "BGPsummary", ["show ip bgp summary"])
        
        # EVPN commands - only for non-host devices
        if "host" not in name:
            collect_command_output(switch, name, outputs, "EVPNsummary", ["show bgp evpn summary"])
            collect_command_output(switch, name, outputs, "EVPN", ["show bgp evpn"])
        
        collect_command_output(switch, name, outputs, "INTsummary", ["show ip interface brief"])
        collect_command_output(switch, name, outputs, "VLANsummary", ["show vlan"])
        
        # Ping tests - only for host1
        if "host1" in name:
            collect_command_output(switch, name, outputs, "PINGsummary", ["ping 172.16.200.20"])
            collect_command_output(switch, name, outputs, "PINGsummary300", ["ping 172.16.30.20"])
        
        # Save all collected outputs to files
        save_outputs_to_files(name, outputs, folder)
        
        print(f"Switch {name} processing complete")


def collect_command_output(switch, name, outputs_dict, output_key, commands):
    """Run a command and store its output with error handling."""
    try:
        result = switch.runCmds(1, ["enable"] + commands, "text")
        outputs_dict[output_key] = result[1]["output"]
        print(f"{name}: Got {output_key}")
    except Exception as e:
        print(f"{name}: Failed to get {output_key}: Most likely this is not enabled or configured")


def collect_mlag_info(switch, name, outputs_dict):
    """Collect MLAG information with error handling."""
    try:
        commands = ["show mlag", "show mlag config-sanity", "show mlag interfaces detail"]
        result = switch.runCmds(1, ["enable"] + commands, "text")
        
        mlag_output = "show mlag\n"
        mlag_output += result[1]["output"]
        mlag_output += "\nshow mlag config-sanity\n"
        mlag_output += result[2]["output"]
        mlag_output += "\nshow mlag interfaces detail\n"
        mlag_output += result[3]["output"]
        
        outputs_dict["MLAG"] = mlag_output
        print(f"{name}: Got MLAG info")
    except Exception as e:
        print(f"{name}: Failed to get MLAG info: Most likely this is not enabled or configured")

def collect_ospf_info(switch, name, outputs_dict):
    """Collect OSPF information with error handling."""
    try:
        commands = ["show ip ospf", "show ip ospf neighbor", "show ip ospf interface brief"]
        result = switch.runCmds(1, ["enable"] + commands, "text")
        
        ospf_output = "show ip ospf\n"
        ospf_output += result[1]["output"]
        ospf_output += "\nshow ip ospf neighbor\n"
        ospf_output += result[2]["output"]
        ospf_output += "\nshow ip ospf interface brief\n"
        ospf_output += result[3]["output"]
        
        outputs_dict["OSPF"] = ospf_output
        print(f"{name}: Got OSPF info")
    except Exception as e:
        print(f"{name}: Failed to get OSPF info: Most likely this is not enabled or configured")

def collect_vxlan_info(switch, name, outputs_dict):
    """Collect VXLAN information with error handling."""
    try:
        commands = [
            "show vxlan address-table", 
            "show mac address-table", 
            "show vxlan flood vtep", 
            "show vxlan vtep detail", 
            "show vxlan vni summary", 
            "show vxlan vni", 
            "show vxlan config-sanity"
        ]
        result = switch.runCmds(1, ["enable"] + commands, "text")
        
        vxlan_output = "show vxlan address-table\n"
        vxlan_output += result[1]["output"]
        vxlan_output += "\nshow mac address-table\n"
        vxlan_output += result[2]["output"]
        vxlan_output += "\nshow vxlan flood vtep\n"
        vxlan_output += result[3]["output"]
        vxlan_output += "\nshow vxlan vtep detail\n"
        vxlan_output += result[4]["output"]
        vxlan_output += "\nshow vxlan vni summary\n"
        vxlan_output += result[5]["output"]
        vxlan_output += "\nshow vxlan vni\n"
        vxlan_output += result[6]["output"]
        vxlan_output += "\nshow vxlan config-sanity\n"
        vxlan_output += result[7]["output"]
        
        outputs_dict["VXLAN"] = vxlan_output
        print(f"{name}: Got VXLAN info")
    except Exception as e:
        print(f"{name}: Failed to get VXLAN info: Most likely this is not enabled or configured")


def save_outputs_to_files(name, outputs_dict, folder):
    """Save all collected outputs to files with error handling."""
    for output_type, content in outputs_dict.items():
        try:
            filename = f"{name}-{output_type}.txt"
            complete_path = os.path.join(folder, filename)
            with open(complete_path, 'w') as f:
                f.write(content)
        except Exception as e:
            print(f"Failed to save {output_type} for {name}: {str(e)}")


def format_command_outputs(commands, results):
    """Format multiple command outputs with headers"""
    output = ""
    for i, cmd in enumerate(commands):
        if i > 0:
            output += "\n"
        output += f"{cmd}\n{results[i]['output']}"
    return output


def save_output_to_file(device_name, output_type, content, folder):
    """Save output content to a file"""
    filename = f"{device_name}-{output_type}.txt"
    complete_path = os.path.join(folder, filename)
    
    with open(complete_path, 'w') as f:
        f.write(content)

def gradeExam(project, instance, region):
    #need gcp project and then url
    grade_url = f"https://us-central1-{project}.cloudfunctions.net/api-grading"
    headers = {'Content-Type': 'application/json'}
    # need instance name and region
    try:
        grade_response = requests.post(url=grade_url,headers=headers,data=json.dumps({"instance":instance,"zone":region,"source":"cli"}))
        if grade_response.status_code == 200:
            marks_dict = grade_response.json()["marks"] # {'V2_L2-Exam': {'score': 10, 'total_points': 195}}
            score, total_points = 0,0
            for lab in marks_dict:
                score = score + marks_dict[lab]['score']
                total_points = total_points + marks_dict[lab]['total_points']
            if score/total_points > 0.75:
                result = "Pass"
            else:
                result = "Fail"
        else:
            raise Exception

    except Exception as e:
        result = "Pending"
    return result
    # then call CF
    # return the exam result

def updateHubspot(result, doceboid, courseid):
    # need docebo user id, course id
    try:
        # get access token
        metadata_url = "http://metadata.google.internal/computeMetadata/v1/project/attributes/hubspot_pat"
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers)
        if response.status_code == 200:
            hubspot_pat = response.text.strip()
        else:
            raise Exception
        # get the record id
        headers = {
        'Authorization': f'Bearer {hubspot_pat}',
        'Content-Type': 'application/json'
        }
        search_url = "https://api.hubapi.com/crm/v3/objects/courses/search"
        filter_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "docebo_user_id",
                            "operator": "EQ",
                            "value": doceboid
                        },
                        {
                            "propertyName": "course_id",
                            "operator": "EQ",
                            "value": courseid
                        }
                    ]
                }
            ]
        }
        search_response = requests.post(url=search_url,data=json.dumps(filter_data),headers=headers)
        # check only one record is returned
        if search_response.status_code == 200:
            record_id = search_response.json()['results'][0]['id']
        else:
            raise Exception
        # update the record with the result
        update_url = f"https://api.hubapi.com/crm/v3/objects/courses/{record_id}"
        update_data = {
            "properties": {
                "recertification_result": result
            }
        }
        update_response = requests.patch(url=update_url,data=json.dumps(update_data),headers=headers)
        if update_response.status_code != 200:
            raise Exception
    except Exception:
        return None




def main():
    labPassword, labTopology, labName, labZone, labProject = readLabDetails()
    #print(labZone)
    allHostsIP, allHostsName = readAtdTopo(labTopology)
    restarted = 0
    folder = str(labName[:-11])
    if not os.path.exists(folder):
        try:
            os.makedirs(folder)
        except OSError as exc: # Guard against race condition
            raise
    grabSwitchDetails(allHostsName,allHostsIP,folder,labPassword)
    tarFile = "results-" + folder #+ "-" + candidateID
    grabCVPInfo(labPassword,folder)
    #createUserFile(fullName,email,candidateID,folder,labName,labTopology)
    print("This file will be created and uploaded " + tarFile)
    with tarfile.open(tarFile, "w:gz") as tar:
        tar.add(os.getcwd() + "/" + folder, arcname=os.path.basename(tarFile))
        tar.add("apps/coder/",arcname=os.path.basename(tarFile))
    #ftpUpload(tarFile)
    #print("Upload complete")
    #grade and update
    if labName.split("-")[2] == "rct":
        print("Grading the exam. Please wait for 1-2 minutes here")
        result = gradeExam(labProject, labName, labZone)
        if result != "Pending":
            userId = labName.split("-")[0][1:]
            courseId = labName.split("-")[1][1:]
            updateHubspot(result,userId,courseId)
            print("Grading completed")
        else:
            print("Contact the support team for the results")
    else:
        result = gradeExam(labProject, labName, labZone)
    print("Disconneting you from the lab environment")
    firewall("block-firewall",labName,labZone)




def firewall(action, instanceName, instanceRegion):
    """
    """
    #get the data from DB if needed
    #call the cloud function or the do the api calls here itself
    response = requests.get(EDIT_INSTANCE + "?function={0}&instance={1}&zone={2}".format(action, instanceName, instanceRegion))
    try:
        print("Access to this lab has been blocked")
        os.system("pkill -KILL -u arista")
        return(response.json())
    except Exception as e:
        print("An error occured, please contact the proctor.")
        return(False)

main()