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
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error


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
EDIT_INSTANCE = 'https://us-central1-{}.cloudfunctions.net/edit-instance'

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

def getUserInfo():
    accept = "n"
    emailCounter = 0
    print("This script requires eAPI to be enabled and functioning. By default this should work, however if any of the settings have been modified it may not work correctly")
    print("You should only upload once you have finished your exam. Once uploaded you will NOT be able to do it again without contacting Arista training or SDN-Pros, as you will be disconneceted from your lab.")
    while accept != "y":
        accept = input ("Do you accept this? (y/n) ")
        if accept == "n":
            sys.exit()
    # fullName = input("Please provide your full name: ")
    # fullName = fullName.replace(' ','')
    # email = input("Please provide your email address: ")
    # while checkEmail(email) == 0:
    #     emailCounter +=1
    #     if emailCounter == 4:
    #         print("You have entered your email incorrect too many times, please restart")
    #         sys.exit()
    #     email = input ("Please provide your email address: ")

    # candidateID = input("Please provide your candidateID: ")
    # return fullName, email, candidateID
    return

# def createUserFile(fullName,email,candidateID,folder,labName,labTopology):
#     topo_id = encodeID({'instance': labName,'topology': labTopology})
#     with open(folder + "/User-Details", 'w') as f:
#          f.writelines(["Name = " + fullName,"\nEmail address = " + email,"\nCandidateID = " + candidateID, "\nLab Name = " + labName,"\nLab Token = " + topo_id,"\n"])
#     print("Written user details to file")


def grabSwitchDetails(allHostsName,allHostsIP,folder,labPassword):
    pingDone = 0
    evpnNOTdone = 0
    for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
        try:
            config = switch.runCmds(1,["enable", "show running-config"],"text")
            runConfig = (config[1]["output"])
            mlag = switch.runCmds(1,["enable", "show mlag","show mlag config-sanity", "show mlag interfaces detail"],"text")
            mlagOutput = "show mlag\n"
            mlagOutput += (mlag[1]["output"])
            mlagOutput += "\nshow mlag config-sanity\n"
            mlagOutput += (mlag[2]["output"])
            mlagOutput += "\nshow mlag interfaces detail\n"
            mlagOutput += (mlag[3]["output"])
            vxlan = switch.runCmds(1,["enable", "show vxlan address-table", "show mac address-table", "show vxlan flood vtep", "show vxlan vtep detail", "show vxlan vni summary", "show vxlan vni", "show vxlan config-sanity"],"text")
            vxlanOutput = "show vxlan address-table\n"
            vxlanOutput += (vxlan[1]["output"])
            vxlanOutput += "\nshow mac address-table\n"
            vxlanOutput += (vxlan[2]["output"])
            vxlanOutput += "\nshow vxlan flood vtep\n"
            vxlanOutput += (vxlan[3]["output"])
            vxlanOutput += "\nshow vxlan vtep detail\n"
            vxlanOutput += (vxlan[4]["output"])
            vxlanOutput += "\nshow vxlan vni summary\n"
            vxlanOutput += (vxlan[5]["output"])
            vxlanOutput += "\nshow vxlan vni\n"
            vxlanOutput += (vxlan[6]["output"])
            vxlanOutput += "\nshow vxlan config-sanity\n"
            vxlanOutput += (vxlan[7]["output"])
            route = switch.runCmds(1,["enable", "show ip route"],"text")
            routeOutput = (route[1]["output"])
            bgpSum = switch.runCmds(1,["enable", "show ip bgp summary"],"text")
            bgpSumOutput = (bgpSum[1]["output"])
            if not "host" in name:
                evpnSum = switch.runCmds(1,["enable", "show bgp evpn summary"],"text")
                evpnSumOutput = (evpnSum[1]["output"])
                evpnBGP = switch.runCmds(1,["enable", "show bgp evpn"],"text")
                evpnBGPOutput = (evpnBGP[1]["output"])
                evpnNOTdone = 1
            intSum = switch.runCmds(1,["enable", "show ip interface brief"],"text")
            intSumOutput = (intSum[1]["output"])
            vlanSum = switch.runCmds(1,["enable", "show vlan"],"text")
            vlanSumOutput = (vlanSum[1]["output"])
            if "host1" in name:
                pingSum = switch.runCmds(1,["enable", "ping 172.16.200.20"],"text")
                pingSumOutput = (pingSum[1]["output"])
                pingDone = 1
                pingSum300 = switch.runCmds(1,["enable", "ping 172.16.30.20"],"text")
                pingSum300Output = (pingSum300[1]["output"])
        except Exception as e:
            print(str(e))
            print("Check eAPI is enabled on {switch}".format(switch = name))
        else:
            filename = str(name) + "-running" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(runConfig)
            filename = str(name) + "-MLAG" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(mlagOutput)
            filename = str(name) + "-Route" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(routeOutput)
            filename = str(name) + "-BGPsummary" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(bgpSumOutput)
            if evpnNOTdone == 1:
                filename = str(name) + "-EVPNsummary" + ".txt"
                completePath = os.path.join(folder, filename)
                with open(completePath, 'w') as f:
                    f.write(evpnSumOutput)
                filename = str(name) + "-EVPN" + ".txt"
                completePath = os.path.join(folder, filename)
                with open(completePath, 'w') as f:
                    f.write(evpnBGPOutput)
                evpnNOTdone = 0
            filename = str(name) + "-INTsummary" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(intSumOutput)
            filename = str(name) + "-VXLAN" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(vxlanOutput)
            filename = str(name) + "-VLANsummary" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(vlanSumOutput)
            if pingDone == 1:
                filename = str(name) + "-PINGsummary" + ".txt"
                completePath = os.path.join(folder, filename)
                with open(completePath, 'w') as f:
                    f.write(pingSumOutput)
                filename = str(name) + "-PINGsummary300" + ".txt"
                completePath = os.path.join(folder, filename)
                with open(completePath, 'w') as f:
                    f.write(pingSum300Output)
                pingDone = 0
            print("Switch {switch} config and output saved".format(switch = name))


def gradeExam(project, instance, region, logger=None):
    """Submit exam for grading"""
    if logger:
        log_operation_start(logger, 'exam-grading', instance=instance, region=region)

    grade_url = f"https://us-central1-{project}.cloudfunctions.net/api-grading"
    headers = {'Content-Type': 'application/json'}

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

            if logger:
                logger.info(f"Exam graded: {result}", extra={'labels': {
                    'operation': 'exam-grading',
                    'instance': instance,
                    'result': result,
                    'score': score,
                    'total_points': total_points
                }})
                log_operation_success(logger, 'exam-grading', instance=instance, result=result, score=score)
        else:
            raise Exception

    except Exception as e:
        result = "Pending"
        if logger:
            log_operation_error(logger, 'exam-grading', str(e), instance=instance, result='Pending')

    return result

def updateHubspot(result, doceboid, courseid, logger=None):
    """Update HubSpot with exam result"""
    if logger:
        log_operation_start(logger, 'hubspot-update', result=result, docebo_user_id=doceboid, course_id=courseid)

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

        if logger:
            log_operation_success(logger, 'hubspot-update', result=result, docebo_user_id=doceboid, course_id=courseid)
        return None
    except Exception as e:
        if logger:
            log_operation_error(logger, 'hubspot-update', str(e), result=result, docebo_user_id=doceboid, course_id=courseid)
        return None




def main():
    # Initialize cloud logging
    logger = setup_cloud_logging('uploadExam')
    logger.info("Starting exam upload process")

    labPassword, labTopology, labName, labZone, labProject = readLabDetails()
    logger.info("Lab details loaded", extra={'labels': {'lab_name': labName, 'topology': labTopology}})

    allHostsIP, allHostsName = readAtdTopo(labTopology)
    restarted = 0
    getUserInfo()
    folder = str(labName[:-11])
    if not os.path.exists(folder):
        try:
            os.makedirs(folder)
        except OSError as exc: # Guard against race condition
            raise

    logger.info("Collecting switch details", extra={'labels': {'lab_name': labName, 'operation': 'exam-submit'}})
    grabSwitchDetails(allHostsName,allHostsIP,folder,labPassword)
    tarFile = "results-" + folder

    grabCVPInfo(labPassword,folder)
    print("This file will be created and uploaded " + tarFile)
    with tarfile.open(tarFile, "w:gz") as tar:
        tar.add(os.getcwd() + "/" + folder, arcname=os.path.basename(tarFile))
        tar.add("apps/coder/",arcname=os.path.basename(tarFile))

    logger.info("Exam data collection complete", extra={'labels': {'lab_name': labName, 'operation': 'exam-submit'}})

    # grade and update
    if labName.split("-")[2] == "rct":
        print("Grading the exam. Please wait for 1-2 minutes here")
        result = gradeExam(labProject, labName, labZone, logger)
        if result != "Pending":
            userId = labName.split("-")[0][1:]
            courseId = labName.split("-")[1][1:]
            updateHubspot(result,userId,courseId, logger)
            print("Grading completed")
        else:
            print("Contact the support team for the results")
    else:
        result = gradeExam(labProject, labName, labZone, logger)

    print("Disconneting you from the lab environment")
    firewall("block-firewall",labName,labZone,labProject, logger)




def firewall(action, instanceName, instanceRegion,labProject, logger=None):
    """Block or unblock firewall access for lab instance"""
    if logger:
        log_operation_start(logger, 'firewall-action', action=action, instance=instanceName)

    response = requests.get(EDIT_INSTANCE.format(labProject) + "?function={0}&instance={1}&zone={2}".format(action, instanceName, instanceRegion))
    try:
        print("Response from edit-instance CF: {}".format(response.json()))
        if logger:
            log_operation_success(logger, 'firewall-action', action=action, instance=instanceName)
        os.system("pkill -KILL -u arista")
        return(response.json())
    except Exception as e:
        print("Response from edit-instance CF: {}".format(e))
        if logger:
            log_operation_error(logger, 'firewall-action', str(e), action=action, instance=instanceName)
        return(False)

main()


# TODO - Include persist folder to TAR file.