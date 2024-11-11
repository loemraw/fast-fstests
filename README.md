# What is fast-fstests?
fast-fstests is a tool that wraps fstests with pytest, opening up possibilities to better integrate with CI/CD pipelines. It leverages Mkosi and QEMU to create virtual machines allowing for isolated test environments. Additionally, fast-fstests can parallelize the execution of fstests across multiple virtual machines, significantly improving testing speed.

# Getting started with fast-fstests!
## fast-fstests relies on:
* [mkosi](https://github.com/systemd/mkosi)
* [QEMU](https://www.qemu.org/download/)
* [mkosi-kernel](https://github.com/DaanDeMeyer/mkosi-kernel) (or other mkosi configuration directory)
* [fstests](https://github.com/kdave/xfstests)
* [pytest](https://docs.pytest.org/en/stable/getting-started.html)
* [pytest-xdist](https://pypi.org/project/pytest-xdist/) (optional for parallel execution)
* [SQLAlchemy](https://www.sqlalchemy.org/) (optional for storing test history)

## Step by step
1. Ensure mkosi is installed
```
mkosi --version
```
If not installed, follow [install instructions](https://github.com/systemd/mkosi).

2. Configure mkosi
```
git clone https://github.com/DaanDeMeyer/mkosi-kernel.git
cd mkosi-kernel
touch mkosi.profiles/fast-fstests.conf
```
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

3. Build the image
```
mkosi --profile fast-fstests -f build
```

4. Ensure everything is configured properly
```
mkosi --profile fast-fstests qemu
cd /fstests
./check
```
If ./check begins running tests everything should be configured properly for fast-fstests!

5. Download repo and install dependencies
```
git clone https://github.com/loemraw/fast-fstests.git
cd fast-fstests
pip install -r requirements.txt
```

6. [Optional] Setup results db
```
python3 src/setup_db.py [PATH TO CREATE RESULTS DB]
```

7. Configure fast-fstests\
Configuration for fast-fstests goes into pytest.ini. Example included at pytest.ini.example.
All fast-fstests options set in pytest.ini can be overriden using command line flags.

| pytest.ini option | command line flag | description |
| :- | :- | -: |
| | -n <br> --numprocesses | Number of virtual machines to run. (requires pytest-xdist) |
| mkosi_config_dir | --mkosi-config-dir | Path to mkosi configuration directory. |
| mkosi_options | --mkosi-options | Options to pass to mkosi when launching qemu. |
| fstests_dir_host | --fstests-dir-host | Path to fstests on host. |
| fstests_dir_machine | --fstests-dir-machine | Path to fstests on virtual machine. |
| tests | --tests | List of tests to run e.g. btrfs/001 or generic/100. (can't be used with group) |
| group | --group | Name of group to run e.g. btrfs/quick or auto. (can't be used with tests) |
| exclude | --exclude | List of tests to exclude. |
| random | --random | Whether to randomize the order that tests are run. |
| results_db_path | --results-db-path | Path to results db. (requires sqlalchemy) |

8. Run fast-fstests
```
pytest -n 5 src/fs_test.py
```
