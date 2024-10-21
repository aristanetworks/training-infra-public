#!/usr/bin/env python
import jsonrpclib
import ssl
import yaml
#static files
labACCESS = '/etc/atd/ACCESS_INFO.yaml'
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
def resetLab(allHosts,labPassword):
    user = 'arista'
    for IPaddress in allHosts:
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = IPaddress))
        try:
            config = switch.runCmds(1,["enable",
                                       "configure",
                                       "aaa authentication login default group atds local",
                                       "ip host apiserver.arista.io 35.192.157.156",
                                       "ip host arista.io 34.67.65.165",
                                       "ip host www.arista.io 34.67.65.165",
                                       "ip host www.cv-staging.corp.arista.io 34.82.61.12",
                                       "ip name-server vrf MGMT 8.8.8.8",
                                       "daemon TerminAttr",
                                       "exec /usr/bin/TerminAttr -cvcompression=gzip-smashexcludes=ale,flexCounter,hardware,kni,pulse,strata,flowtracking/hardware -ingestexclude=/Sysdb/cell/1/agent,/Sysdb/cell/2/agent -cvaddr=192.168.0.5:9910 -cvauth=token,/tmp/token -cvvrf=MGMT -taillogs -disableaaa",
                                       "no shutdown",
                                       "aaa authorization exec default group atds local",
                                       "aaa authorization commands all default local",
                                       "write memory"
                                       ],"text")
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
def main():
    labPassword, labTopology = readLabDetails()
    allHosts = readAtdTopo(labTopology)
    resetLab(allHosts,labPassword)
main()