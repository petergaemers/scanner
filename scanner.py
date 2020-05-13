print('Please stand-by submission is initalized, this may take a moment.\n')
import json
import logging
import os
import random 
import shutil
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict


import numpy as np

import straxen

#SBATCH --job-name=scanner_{name}_{config}                                                             
# Previous issue was that the job-name was too long. 
# There's some quota on how many characters or something a job-name can be.
# mem-per-cpu argument doesn't work on dali not sure why. Too many computers requested I believe.
JOB_HEADER = """#!/bin/bash
#SBATCH --job-name=scan_{name}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={n_cpu}
#SBATCH --time={max_hours}:00:00
#SBATCH --partition={partition}
#SBATCH --account=pi-lgrandi
#SBATCH --qos={partition}
#SBATCH --output={log_fn}
#SBATCH --error={log_fn}
{extra_header}
# Conda
. "{conda_dir}/etc/profile.d/conda.sh"
{conda_dir}/bin/conda activate {env_name}
echo Starting scanner
python {python_file} {run_id} {data_path} {config_file} 
"""


def scan_parameters(target,
                    parameter,
                    name,
                    register=None,
                    output_directory='./strax_data',
                    job_config=None,
                    log_directory='./parameter_scan',
                    xenon1t=False,
                    **kwargs):
    """Called in mystuff.py to run specified jobs. 
    
    This main function goes through and runs jobs based on the options 
    given in strax. Currently this is constructed to call 
    submit_setting, which then eventually starts a job and proceeds to 
    get a dataframe of event_info, thereby processing all data for 
    specified runs. 
    
    Params: 
    strax_options: a dictionary which must include:
        - 'run_id' (default is '180215_1029')
        - 'config' (strax configuration options)
        - A directory to save the job logs in. Default is current 
            directory in a new file.
        - kwargs: max_hours, extra_header, n_cpu, ram (typical dali job 
            sumission arguments apply.)
    """
    assert 'run_id' in parameter.keys(), 'No run_id key found in parameters.' 
    # List of todos, or things we could change as well:
    #TODO: this function does not support a chnage of context yet. 
    #        * But should be possible to make it an argument.
    config_list = _make_config(parameter)
    
    # I guess it will happen from time to time that somebody messes up....
    # So let us people explicitly confirm their submission before they start
    # hundrets of jobs.... 
    _user_check()
    
    # Displaying the job-settings: as well:
    default_job_config = {'job_name': name,
                          'n_cpu': 4,
                          'max_hours': 8,
                          'mem-per-cpu': 4480,
                          'partition': 'dali',
                          'conda_dir': '/dali/lgrandi/strax/miniconda3',
                          'extra_sbatch_header': ''
                         }
    # Update default settings:
    if job_config:
        for key, value in job_config.items():
            if key in default_job_config.keys():
                default_job_config['key'] = value
            else:
                raise ValueError(f'Job settings {key} is not supported.')
    
    # Lets print the settings and ask the user again:
    print('\nYou specified the following settings for the batch jobs:')
    for key, value in default_job_config.items():
        print(f'{key}:   {value}')
    _user_check()
    return 0
    
    
    # Check that directory exists, else make it
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    # Submit each of these, such that it calls me (parameter_scan.py) with submit_setting option
    for i, config in enumerate(config_list):
        print('Submitting %d with %s' % (i, config))
        submit_setting(target,
                       name,
                       register,
                       config,
                       output_directory,
                       default_job_config,
                       log_directory,
                       xenon1t,
                       **kwargs
                      )
    pass


def _user_check():
    not_correct_answer = True
    answer = input('Are you sure you would like to submit these settings? (y/n)')
    while not_correct_answer:
        if answer=='y':
            print('Proceeding with the submission.')
            not_correct_answer = False
        elif answer=='n':
            print('Abord submission.')
            raise SystemExit(0)
        else:
            answer = input(f'{answer} was not a valid input please use (y/n) for yes/no.')
    

def _make_config(parameters_dict):
    # Converting any dict to ordered dict, might be a bit more user 
    # friendly.    
    parameters = OrderedDict()
    for key, values in parameters_dict.items():
        print(key, values)
        parameters[key] = values

    keys = list(parameters.keys())
    values = list(parameters.values())

    #Make a meshgrid of all the possible parameters we have, for scanning purposes.
    combination_values = np.array(np.meshgrid(*values)).T.reshape(-1,
                                                                  len(parameters))

    strax_options = []

    #Enumerate over all possible options to create a strax_options list for scanning later.
    for i, value in enumerate(combination_values):
        print('Setting %d:' % i)
        config = {}
        for j, parameter in enumerate(value):
            print('\t', keys[j], parameter)
            config[keys[j]] = parameter
        strax_options.append(config)
    return strax_options


def submit_setting(run_id, # do we need this line? Don't think it should be in here. 
                   target,
                   name,
                   register,
                   config,
                   output_directory,
                   job_config,
                   log_directory,
                   xenon1t,
                   **kwargs):
    """
    Submits a job with a certain setting.
    
    Given input setting arguments, submit_setting will take a current 
    run and submit a job to dali that calles this code itself (so not 
    runs scanner.py directly.) First, files are created temporarily in 
    directory (arg) that document what you have submitted. These are 
    then used to submit a typical job to dali. This then bumps us down 
    to if __name__ == '__main__' since we call it directly. You could 
    alternatively switch this so that if we submit_setting we run a 
    different file. 
    
    Arguments:
    run_id (str): run id of the run to submit the job for. Currently not 
        tested if multiple run id's work but they should.
    config (dict): a dictionary of configuration settings to try (see 
        strax_options in mystuff.py for construction of these config 
        settings)
    directory (str): Place where the job logs will get saved. These are 
        short files just with the parameters saved and log of what 
        happened, but can be useful in debugging.
    
    **kwargs (optional): can include the following
        - n_cpu : numbers of cpu for this job. Default 40. Decreasing 
            this to minimum needed will get your job to submit to dali
            faster!
        - max_hours: max hours to run job. Default 8
        - name: the appendix you want to scan_{name} which shows up in 
            dali. Defaults "magician" if not specified so change if 
            you're not a wizard.
        - mem_per_cpu: (MB) default is 4480 MB for RAM per CPU. May need 
            more to run big processing.
        - partition: Default to dali
        - conda_dir: Where is your environment stored? Default is 
            /dali/lgrandi/strax/miniconda3 (for backup strax env). 
            Change?
        - env_name: Which conda env do you want, defaults to strax 
            inside conda_dir
    """
    
    job_fn = tempfile.NamedTemporaryFile(delete=False,
                                         dir=log_directory).name
    log_fn = tempfile.NamedTemporaryFile(delete=False,
                                         dir=log_directory).name

    config_fn = tempfile.NamedTemporaryFile(delete=False,
                                            dir=log_directory).name
    
    #Takes configuration parameters and dumps the stringed version into a file called config_fn
    with open(config_fn, mode='w') as f:
        json.dump(config, f)

    with open(job_fn, mode='w') as f:
        # Rename such that not just calling header, I think this is done now, no?
        # TODO PEP8
        f.write(JOB_HEADER.format(
            **job_config,
            log_fn=log_fn,
            python_file=os.path.abspath(__file__),
            config_file = config_fn,
            name=name,
            run_id=config['run_id'],
            data_path=output_directory, 
        ))
    print(sys.argv)

    print("\tSubmitting sbatch %s" % job_fn)
    result = subprocess.check_output(['sbatch', job_fn])

    print("\tsbatch returned: %s" % result)
    job_id = int(result.decode().split()[-1])

    print("\tYou have job id %d" % job_id)


def work(run_id, 
         target,  
         config, 
         output_folder='./strax_data',
         register=None, 
         xenon1t=False,
         **kwargs):
    if xenon1T:
        # XENON1T env is not as flexiable as the nT env...
        st = straxen.context.xenon1t_dali(output_folder=output_folder)
        st.set_config(**config)
        st.register(register)
        st.make(run_id, target, max_worker=kwargs.get('n_cpu', 4), **kwargs)
    else:
        st = straxen.contexts.xenonnt_online(register=register,
                                             output_folder=output_folder,
                                             config=config
                                            )
        st.make(run_id, target, max_worker=kwargs.get('n_cpu', 4), **kwargs)
    
    

if __name__ == "__main__": #happens if submit_setting() is called
    if len(sys.argv) == 1: # argv[0] is the filename
        print('hi I am ', __file__)
        scan_parameters()
    elif len(sys.argv) == 5:
        run_id = sys.argv[1]
        target = sys.argv[2]
        config_fn = sys.argv[3]
        output_folder = sys.argv[4]
        print(run_id, data_path, config_fn)
        print("Things are changing")
        # Reread the config file to grab the config parameters
        with open(config_fn, mode='r') as f:
            config = json.load(f)
        work(run_id=run_id, 
             target=target, 
             register=config['register'], 
             output_folder=data_path, 
             config=config)
        
    else:
        raise ValueError("Bad command line arguments")
