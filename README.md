# What is fast-fstests?
fast-fstests is a tool that wraps fstests with pytest,
opening up possibilities to better integrate with CI/CD pipelines.
It leverages multiple machines allowing for parallel testing.
This can be configured optionally with mkosi to automatically
handle the creation and destruction of vms.

# Getting started with fast-fstests!
## fast-fstests relies on:
* [fstests](https://github.com/kdave/xfstests)
* [pytest](https://docs.pytest.org/en/stable/getting-started.html)
## fast-fstests optionally uses:
* [mkosi](https://github.com/systemd/mkosi) - for managing virtual machines
* [mkosi-kernel](https://github.com/DaanDeMeyer/mkosi-kernel) - for configuring mkosi
* [pytest-xdist](https://pypi.org/project/pytest-xdist/) - for parallelizing pytest
* [SQLAlchemy](https://www.sqlalchemy.org/) - for keeping track of test results

# fast-fstests configuration
* fast-fstests can be configured via a pytest.ini file or via cli arguments.
* Example included at pytest.ini.example.
* fast-fstests options set in pytest.ini are overriden by command line flags
unless it's a list argument in which case command line flags will append to pytest.ini options.

| pytest.ini option | command line flag | description |
| :- | :- | -: |
| targetpaths | --targetpath | Specify targetpaths to run fstests on.<br>HOSTNAME:PATH-TO-FSTESTS eg. vm1:/home/fstests |
| mkosi | --mkosi | Specify the number of mkosi hosts to create. |
| mkosi_config_dir | --mkosi-config-dir | Path to mkosi configuration directory. |
| mkosi_options | --mkosi-option | Options to pass to mkosi when launching qemu. |
| mkosi_options | --mkosi-option | Options to pass to mkosi when launching qemu. |
| mkosi_fstests_dir | --mkosi-fstests-dir | Path to fstests on mkosi. |
| host_fstests_dir | --host-fstests-dir | Path to fstests on host. |
| tests | --test | List of tests to run e.g. btrfs/001 or generic/100. (can't be used with group) |
| group | --group | Name of group to run e.g. btrfs/quick or auto. (can't be used with tests) |
| excludes | --exclude | List of tests to exclude. |
| random | --random | Whether to randomize the order that tests are run. |
| results_db_path | --results-db-path | Path to results db. |

# Run fast-fstests
```
pip install -r requirements.txt
pytest src/fast-fstests.py --targetpath host1:/fstests --group btrfs/auto
```

# Running with mkosi
Though mkosi is optional I find the convenience of not managing vms very appealing.
Here are directions for getting everything setup with mkosi.
Caveat this guide is not intended as a comprehensive mkosi setup guide,
it is only detailing some additional configuration needed to get mkosi working with fast-fstests.
It is highly recommended that before starting this guide you already have a working
mkosi and mkosi-kernel setup that can run fstests.

1. mkosi-kernel profile
I like to create a separate profile to hold all of the mkosi configurations for running fast-fstests.
I've included an example in fast-fstests.conf.example. Some important configurations are:
* Ssh=yes\
  (necessary to remotely execute fstests on vm)
* Ephemeral=yes\
  (necessary to launch multiple vms from the same image)
* Include=modules/fstests\
  (include the fstests module)
* BuildSources=[PATH TO FSTESTS]:fstests\
  (tell mkosi where fstests is on your machine)
* ExtraTrees=[PATH TO MKOSI-KERNEL]/mkosi.builddir/centos\~9~x86-64/fstests:/fstests\
  (copies the built fstests to /fstests on the vm)

2. Build the image
```
mkosi --profile fast-fstests -f build
```

3. Check if fstests runs
```
mkosi --profile fast-fstests qemu
cd .../fstests
./check
```
./check should start running tests without needing to compile anything.

4. Check if ephemeral machines are working
```
mkosi --profile fast-fstests --machine 1 qemu
```
In a separate shell:
```
mkosi --profile fast-fstests --machine 2 qemu
```
Both should launch successfully.

5. Check if ssh is working
```
mkosi --profile fast-fstests qemu
```
Once qemu vm is up and running, run this in a separate shell:
```
mkosi ssh
```
Should successfully ssh into qemu vm.

6. Install dependencies
Make sure pytest, pytest-xdist are installed.
```
cd .../fast-fstests
pip install pytest
pip install pytest-xdist
```

7. If all steps above are working you should be good to go!
```
pytest src/fast-fstests.py --mkosi 5 --group btrfs/auto
```

# Results DB

1. Install dependency
```
cd .../fast-fstests
pip install sqlalchemy 
```

2. Setup results db
```
python3 src/setup_db.py [PATH TO CREATE RESULTS DB]
```

3. Configure fast-fstests
In pytest.ini add\
```
results_db_path=[PATH TO RESULTS DB]
```

4. [TODO] CLI for interacting with test history
