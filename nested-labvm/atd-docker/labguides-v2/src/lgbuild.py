#!/bin/env python3


import os, sys, yaml
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


lab_count=0

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
    if os.path.isfile(src):
        if os.path.isfile(dest):
            print ("Skipping file - dest = ", dest, " - already exists")
            os.remove(src)
        else:
            print ("Merging file - src = ", src, ": dest = ", dest)
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
    try:
        clone=Repo.clone_from(git_repo_url, git_dir, branch=item["branch"])
    except:
        clone=False
    if not clone:
        print ('Failed to clone repo ', item["repo"], ' : Branch ',item["branch"])
        rmtree (build_dir)
        sys.exit(0)


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


def add_lab(module):
    global lab_count
    #clone the repo
    name=module.split(':')[0]
    repo=repo_base+name+".git"
    if len(module.split(':'))>1:
        branch=module.split(':')[1]
    else:
        branch="main"
    git({'repo':repo,'branch':branch})

    #read metadata file
    try:
        with open(os.path.join(git_dir,'metadata.yaml')) as f: metadata=yaml.safe_load(f)
        file=metadata['file']
        if type(file)!=list: file=[file]
        caption=metadata['caption']
    except:
        file=name+'.rst'
        caption=''

    #remove duplicates
    for duplicate in file:
        for already_there in build['files']:
            if duplicate.lower()==already_there.lower():
                print ("Skipping TOC entry - ", duplicate)
                os.remove(os.path.join(git_dir,duplicate))
                file.pop(file.index(duplicate))
                if len(file)>0:duplicate=file[0]

    #write out
    if len(file)>0:
        if lab_count>0:
            if 'exam' not in build['index']['name'].lower(): caption=str('Lab '+str(lab_count)+' - '+caption)
        toc=str("\n.. toctree::\n   :hidden:\n   :maxdepth: 1\n   :caption: "+caption+"\n\n")
        print ("Adding TOC section - ",caption)
        write_to_index(index_file,toc)
        for element in file:
            print ("Adding TOC entry - ",element)
            write_to_index(index_file,"   "+element+'\n')
    #merge
        merge()
    else:
        print ("Skipping TOC section - ", caption)
        lab_count-=1
        print ("Reduced lab_count to ",lab_count)
        rmtree (git_dir)


if __name__ == '__main__':
    try:
        ACCESS_INFO=load_ACCESS_INFO()
        if type(ACCESS_INFO['labguides_modules']) != list: raise()
        if len(ACCESS_INFO['labguides_modules'])<2: raise()
    except:
        sys.exit(0)
    try:
        if 'lgdeploy' in ACCESS_INFO['labguides_modules'][0]:
            build['deploy']['branch']=ACCESS_INFO['labguides_modules'].pop(0).split(':')[1]
    except:
        print('Could not set branch name for lgdeploy repo, using default')
    git(build['deploy'])
    merge()
    build_index(list.pop(ACCESS_INFO['labguides_modules'],0))
    index_file=os.path.join(build_dir,build['index']['file'])
    build.update({'files':[build['index']['file']]})
    for module in ACCESS_INFO['labguides_modules']:
        if 'labaccess' not in module.lower(): lab_count+=1
        add_lab(module)
    call(['make', '-C', build_dir,'html'])
    rmtree (build_dir)
