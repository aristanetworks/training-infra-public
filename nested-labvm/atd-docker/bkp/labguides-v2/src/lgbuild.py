#!/bin/env python3

import os
try:
    import sys, yaml, requests
    from contextlib import suppress
    from shutil import move, rmtree
    from subprocess import call
    from git import Repo

#################################################   SETUP global variables   ##########################################
    with open(os.path.join(os.getcwd(),'deploy.yaml')) as f: deploy_data=yaml.safe_load(f)

    repo_base=deploy_data['repo_base']

    build={}
    build.update({"dest_dir":deploy_data['dest_dir']})
    build.update({"deploy":{"name":deploy_data['deploy']['name'],"file":deploy_data['deploy']['file'],"repo":repo_base+deploy_data['deploy']['repo'],"branch":deploy_data['deploy']['branch']}})

    index_file=''
    lab_count=0
    combined_lab_list = {}
    combined_lab_configlets = {}

    git_dir=(os.path.join(os.getcwd(), 'git'))
    if os.path.exists(git_dir): rmtree(git_dir)

    build_dir=(os.path.join(os.getcwd(), 'build'))
    if not os.path.exists(build_dir): os.mkdir(build_dir)

    html_dir=(os.path.join(os.getcwd(), 'html'))
    menus_dir=(os.path.join(os.getcwd(), 'menus'))
except Exception as e:
    err='Failed to load the Lab Guide Build Engine - '+str(e.args)
    print (err)
    try:
        with open(os.path.join(os.path.sep,'menus','labguides-done.txt'), 'w') as output_file: output_file.write(err)
        print ("Released the UI Landing Container")
    except Exception as e:
        print ("Failed to release the UI Landing container - ",e.args)
    sys.exit(0)


try:
    metadata_url = "http://metadata.google.internal/computeMetadata/v1/project/attributes/github_sdnpros_pat_key"
    headers = {"Metadata-Flavor": "Google"}
    response = requests.get(metadata_url, headers=headers)
    if response.status_code == 200:
        github_key= response.text.strip()
    else:
        print("Failed to retrieve GitHub PAT Key:", response.status_code)
        raise ()
except:
    sys.exit(1)


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


def clean_dir(dir):
    for item in os.listdir(dir):
        item=os.path.join(dir, item)
        if os.path.isfile(item): os.unlink(item)
        else: rmtree(item)


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
    except Exception as e:
        print ('Failed to clone repo ', item["repo"])
        except_out(0,'Failed to clone the repo - '+str(e.args))


def build_index(index):
    global combined_lab_list, combined_lab_configlets
    lab_list=False
    labconfiglets=False
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
        with suppress(KeyError): lab_list=metadata['lab_list']
        with suppress(KeyError): labconfiglets=metadata['labconfiglets']
    except:
        file='index.rst'
        caption=None
    try:
        os.path.getsize(os.path.join(git_dir,file))
    except Exception as e:
        print ('Failed to build the index')
        except_out(0,'Failed to build index - '+str(e.args))
    build.update({"index":{"name":name,"file":file,"repo":repo,"branch":branch}})
    merge()

    #add menu items
    if lab_list:
        print ("Merging lab_list: ",lab_list)
        combined_lab_list = update_menu_dict(combined_lab_list, lab_list)
    if labconfiglets:
        print ("Merging labconfiglets: ",labconfiglets)
        combined_lab_configlets = update_menu_dict(combined_lab_configlets, labconfiglets)


def update_menu_dict(original_dict, new_entries):
    global combined_lab_list, combined_lab_configlets
    # ordered dictionary
    updated_dict = {}
    # add reset entries first if they don't exist in the original dict
    for key, value in new_entries.items():
        if 'reset' in key.lower() and key not in original_dict:
            updated_dict[key] = value
    # add others
    updated_dict.update(original_dict)
    # add new non-reset 
    for key, value in new_entries.items():
        if key not in original_dict and 'reset' not in key.lower():
            updated_dict[key] = value
    return updated_dict


def add_lab(module):
    global lab_count, combined_lab_list, combined_lab_configlets

    #clone the repo
    name=module.split(':')[0]
    repo=repo_base+name+".git"
    if len(module.split(':'))>1:
        branch=module.split(':')[1]
    else:
        branch="main"
    git({'repo':repo,'branch':branch})

    #read metadata file
    unnumbered=lab_list=labconfiglets=False
    try:
        with open(os.path.join(git_dir,'metadata.yaml')) as f: metadata=yaml.safe_load(f)
        file=metadata['file']
        if type(file)!=list: file=[file]
        with suppress(KeyError): caption=metadata['caption']
        with suppress(KeyError): unnumbered=bool(metadata['unnumbered'])
        with suppress(KeyError): title=metadata['title']
        with suppress(KeyError): lab_list=metadata['lab_list']
        with suppress(KeyError): labconfiglets=metadata['labconfiglets']
    except:
        file=name+'.rst'
        caption=''
        try:
            os.path.getsize(os.path.join(git_dir,file))
        except Exception as e:
            print ('Failed to load metatdata')
            except_out(0,'Failed to load the repo metadata - '+str(e.args))

    #remove duplicate TOC entries
    for duplicate in file:
        for already_there in build['files']:
            if duplicate.lower()==already_there.lower():
                print ("Skipping TOC entry - ", duplicate)
                os.remove(os.path.join(git_dir,duplicate))
                file.pop(file.index(duplicate))
                if len(file)>0:duplicate=file[0]

    #write to index file
    if len(file)>0:
        if lab_count>0:
            try:
                if not unnumbered and title and caption: caption=str(title + ' ' + str(lab_count) + ' - ' + caption)
                elif unnumbered and title and caption: caption=str(title + ' - ' + caption)
                elif not unnumbered and not title and caption: caption=str(str(lab_count) + ' - ' + caption)
                else: None
            except:
                print ("Using old logic to parse TOC section - Missing unnumbered or caption fields from metadata")
                if 'exam' not in build['index']['name'].lower(): caption=str('Lab '+str(lab_count)+' - '+caption)
        toc=str("\n.. toctree::\n   :hidden:\n   :maxdepth: 1\n   :caption: "+caption+"\n\n")
        print ("Adding TOC section - ",caption)
        write_to_index(index_file,toc)
        for element in file:
            print ("Adding TOC entry - ",element)
            write_to_index(index_file,"   "+element+'\n')
        if not unnumbered: lab_count+=1
        #index complete - merge files into build_dir
        merge()
    else:
        print ("Skipping TOC section - ", caption)
        lab_count-=1
        print ("Reduced lab_count to ",lab_count)
        rmtree (git_dir)

    #add menu items
    if lab_list:
        print ("Merging lab_list: ",lab_list)
        combined_lab_list = update_menu_dict(combined_lab_list, lab_list)
    if labconfiglets:
        print ("Merging labconfiglets: ",labconfiglets)
        combined_lab_configlets = update_menu_dict(combined_lab_configlets, labconfiglets)


def build_labguide(ACCESS_INFO):
    global build, index_file
    clean_dir(build_dir)
#    clean_dir(menus_dir)
    git(build['deploy'])
    merge()
    build_index(list.pop(ACCESS_INFO['labguides_modules'],0))
    index_file=os.path.join(build_dir,build['index']['file'])
    build.update({'files':[build['index']['file']]})
    for module in ACCESS_INFO['labguides_modules']:
        add_lab(module)
    #clean current html directory
    clean_dir(html_dir)
    #make labguide
    call(['make', '-C', build_dir,'html'])
    #clean build directory
    rmtree (build_dir)
    #output training.yaml file
    if len(combined_lab_list)>0 and len(combined_lab_list)==len(combined_lab_configlets):
        print ("Writing training.yaml file")
        with open(os.path.join(menus_dir,'training.yaml'), 'w') as output_file:
            output_file.write('---\n')
            yaml.safe_dump({'lab_list': combined_lab_list,'labconfiglets': combined_lab_configlets}, output_file, default_flow_style=False, sort_keys=False)
    #output default.yaml file
    if os.path.exists(os.path.join(menus_dir,'training.yaml')):
        print("Writing the default.yaml file")
        with open(os.path.join(menus_dir,'default.yaml'), 'w') as output_file:
            output_file.write('---\n')
            yaml.safe_dump({'default_menu': 'training.yaml'}, output_file)
    #release UI Landing
    release_ui_landing('Lab Guide Build Engine completed with no errors')


def release_ui_landing(response):
    print ('releasing UI landing')
    with open(os.path.join(menus_dir,'labguides-done.txt'), 'w') as output_file:
        output_file.write(response)


def except_out(response_code,response):
    print ('exception', response)
    with suppress(Exception): rmtree (build_dir)
    with suppress(Exception): rmtree (git_dir)
    if response_code==0: release_ui_landing(response)
    sys.exit(response_code)


if __name__ == '__main__':
    try:
        ACCESS_INFO=load_ACCESS_INFO()
        if type(ACCESS_INFO['labguides_modules']) != list: raise()
        if len(ACCESS_INFO['labguides_modules'])<2: raise()
    except Exception as e:
        except_out(0,'Failed to load ACCESS_INFO data - '+str(e.args))
    try:
        if 'lgbuild_engine' in ACCESS_INFO['labguides_modules'][0]:
            from importlib.machinery import SourceFileLoader
            build.update({"lgbuild":{"repo":repo_base+'lgbuild_engine.git',"branch":ACCESS_INFO['labguides_modules'].pop(0).split(':')[1]}})
            git(build['lgbuild'])
            lgbuild_update=SourceFileLoader("lgbuild_update", os.path.join(git_dir,'docker-entrypoint-lgbuild/lgbuild.py')).load_module()
            from lgbuild_update import *
    except:
        print('Could not update lgbuild_engine, using default')
    try:
        if 'lgdeploy' in ACCESS_INFO['labguides_modules'][0]:
            build['deploy']['branch']=ACCESS_INFO['labguides_modules'].pop(0).split(':')[1]
    except:
        print('Could not set branch name for lgdeploy repo, using default')
    try:
        build_labguide(ACCESS_INFO)
    except Exception as e:
        except_out(0,'Failed to build the Lab Guide - '+str(e.args))
