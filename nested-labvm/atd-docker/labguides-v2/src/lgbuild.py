#!/bin/env python3


import os, sys, json, yaml 
from shutil import move, rmtree
from subprocess import call
from git import Repo
import requests



with open(os.path.join(os.getcwd(),'deploy.yaml')) as f: deploy_data=yaml.safe_load(f)
repo_base=deploy_data['repo_base']
build={}
build.update({"dest_dir":deploy_data['dest_dir']})
build.update({"deploy":{"name":deploy_data['deploy']['name'],"file":deploy_data['deploy']['file'],"repo":repo_base+deploy_data['deploy']['repo'],"branch":deploy_data['deploy']['branch']}})
metadata_url = "http://metadata.google.internal/computeMetadata/v1/project/attributes/github_sdnpros_pat_key"
headers = {"Metadata-Flavor": "Google"}
response = requests.get(metadata_url, headers=headers)
if response.status_code == 200:
    github_key= response.text.strip()
else:
    print("Failed to retrieve GitHub PAT Key:", response.status_code)


git_dir=(os.path.join(os.getcwd(), 'git'))
if os.path.exists(git_dir): rmtree(git_dir)

build_dir=(os.path.join(os.getcwd(), 'build'))
if not os.path.exists(build_dir): os.mkdir(build_dir)

html_dir=(os.path.join(os.getcwd(), 'html'))


def load_ACCESS_INFO():
    with open(os.path.join(os.getcwd(),'ACCESS_INFO.yaml')) as ACCESS_INFO:
        return yaml.safe_load(ACCESS_INFO)


def write_to_index(index_file,text):
    with open(index_file, 'a') as index:
        index.write(text)


def move_item(src):
    dest=src.replace(git_dir+os.path.sep,build_dir+os.path.sep)
    print ("Merging file - src = ", src, ": dest = ", dest)
    if os.path.isfile(src):
        move(src,dest)
    else:
        if not os.path.exists(dest): os.mkdir(dest)
        for subsrc in os.listdir(src):
            move_item(os.path.join(src,subsrc))


def merge():
    for src in os.listdir(git_dir):
        if src[0] != '.':
            if 'metadata' not in src.lower():
                move_item(os.path.join(git_dir, src))
    rmtree (git_dir)


def git(item):
    git_repo_url = f'https://lab-guide-device:{github_key}@{item["repo"]}'
    Repo.clone_from(git_repo_url, git_dir)

def build_index(index):
    name=index.split(':')[0]
    repo=repo_base+name+".git"
    if len(index.split(':'))>1:
        branch=index.split(':')[1]
    else:
        branch="main"
    git({'repo':repo,'branch':branch,})
    try:
        with open(os.path.join(git_dir,'metadata.yaml')) as f: metadata=yaml.safe_load(f)
        file=metadata['file']
    except:
        file='index.rst'
        caption=None
    build.update({"index":{"name":name,"file":file,"repo":repo,"branch":branch}})
    merge()


def add_lab(module,lab_count):
    #git repo
    name=module.split(':')[0]
    repo=repo_base+name+".git"
    if len(module.split(':'))>1:
        branch=module.split(':')[1]
    else:
        branch="main"
    git({'repo':repo,'branch':branch,})
    #metadata file
    try:
        with open(os.path.join(git_dir,'metadata.yaml')) as f: metadata=yaml.safe_load(f)
        file=metadata['file']
        caption=metadata['caption']
    except:
        file=name+'.rst'
        caption=''
    #write out
    if lab_count>0: 
        if 'exam' not in build['index']['name'].lower(): caption=str('Lab '+str(lab_count)+' - '+caption) 
    toc=str("\n.. toctree::\n   :hidden:\n   :maxdepth: 1\n   :caption: "+caption+"\n\n")
    write_to_index(index_file,toc)
    if type(file)==list:
        for element in file:
            write_to_index(index_file,"   "+element+'\n')
    else:
        write_to_index(index_file,"   "+file+'\n')
#merge
    merge()


if __name__ == '__main__':
    ACCESS_INFO=load_ACCESS_INFO()
    git(build['deploy'])
    merge()
    build_index(list.pop(ACCESS_INFO['labguides_modules'],0))
    index_file=os.path.join(build_dir,build['index']['file'])
    lab_count=0
    for module in ACCESS_INFO['labguides_modules']:
        if 'labaccess' not in module.lower(): lab_count+=1
        add_lab(module,lab_count)
    call(['make', '-C', build_dir,'html'])
    rmtree (build_dir)
