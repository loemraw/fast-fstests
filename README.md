# What is fast-fstests?
fast-fstests is a tool that parallelizes fstests across virtual machines

# How much faster?
Runtime data in seconds collected on my machine.
| group | fstests (s) | fast-fstests 5vms (s) | fast-fstests 10vms (s) | fast-fstests 15vms (s) | fast-fstests 20vms (s) | fast-fstests 25vms (s) | fast-fstests 30vms (s) |
| - | - | - | - | - | - | - | - |
| auto* | 5780 | 1620 | 1090 | 920 | 870 | 840 | 950 |
| quick | 1500 | 680 | 540 | 490 | 435 | 425 | 450 |
| btrfs/auto* | 1470 | 430 | 310 | 255 | 310 | 310 | 285 |
| btrfs/quick | 390 | 170 | 120 | 125 | 105 | 110 | 110 |

*I excluded btrfs/187 and generic/562 as they are outliers that take around 30min on my machine.

There is between a 3-7x speed improvement, bringing down the time to run auto from 96 minutes to 14 minutes.


# Getting started with fast-fstests!
## fast-fstests relies on:
* [fstests](https://github.com/btrfs/fstests)
* [mkosi](https://github.com/systemd/mkosi) - for managing virtual machines
* [mkosi-kernel](https://github.com/DaanDeMeyer/mkosi-kernel) - for configuring mkosi

## installation
```bash
git clone https://github.com/loemraw/fast-fstests.git
cd fast-fstests
pip install -e .
# or if you want to plots in output
pip install -e ".[plot]"
fast-fstests --help
```

## configuration
* fast-fstests can be configured via a config.toml file or via cli arguments
* config file path may be changed via environment variable `FAST_FSTESTS_CONFIG_PATH`
* example included at config.toml.example
* cli flags always override config.toml options

### Top-Level

| Option         | Type      | CLI Argument(s)         | Description                                 |
|----------------|-----------|-------------------------|---------------------------------------------|
| `fstests`      | Path      | `--fstests`             | **Required.** Path to the fstests directory.|

### `[test_selection]` Section

| Option              | Type         | CLI Argument(s)                 | Description                                 |
|---------------------|--------------|---------------------------------|---------------------------------------------|
| `tests`             | list[str]    | `[TEST...]`                     | List of tests to run.                       |
| `groups`            | list[str]    | `--groups`, `-g`                | List of groups to include.                  |
| `exclude_tests`     | list[str]    | `--exclude-tests`, `-e`         | List of tests to exclude.                   |
| `exclude_tests_file`| Path         | `--exclude-tests-file`, `-E`    | Path to file with tests to exclude.         |
| `exclude_groups`    | list[str]    | `--exclude-groups`, `-x`        | List of groups to exclude.                  |
| `section`           | str          | `--section`, `-s`               | Only include specific section.              |
| `exclude_section`   | str          | `--exclude-section`, `-S`       | Exclude specific section.                   |
| `randomize`         | bool         | `--randomize`, `-r`             | Randomize test order.                       |
| `iterate`           | int          | `--iterate`, `-i`               | Number of times to run each test.           |
| `list`              | bool         | `--list`, `-l`                  | List tests without running any.             |
| `file_system`       | str          | `--file-system`                 | Specify file system to be tested (equivalent to -btrfs or -xfs for ./check) |

### `[test_runner]` Section
| Option      | Type      | CLI Argument(s)         | Description                                 |
|-------------|-----------|-------------------------|---------------------------------------------|
| `keep_alive`   | bool      | `--keep-alive`, `--no-keep-alive`          | Keep hosts alive for debugging.             |
| `test_timeout` | int | `--test-timeout` | Max seconds to run a test. |
| `bpftrace` | str | `--bpftrace` | BPFTrace script to be executed with -e |
| `bpftrace_script` | Path | `--bpftrace-script` | BPFTrace script path that will be executed on vm |

### `[mkosi]` Section

| Option      | Type      | CLI Argument(s)         | Description                                 |
|-------------|-----------|-------------------------|---------------------------------------------|
| `num`       | int       | `--mkosi.num`, `-n`     | Number of mkosi VMs to spawn.               |
| `config`    | Path      | `--mkosi.config`        | **Required if using mkosi** Path to mkosi config.  |
| `options`   | list[str] | `--mkosi.options`       | List of options for mkosi.                  |
| `fstests`   | Path      | `--mkosi.fstests`       | **Required if using mkosi** Path to fstests dir on mkosi VM.            |
| `timeout`   | int       | `--mkosi.timeout`       | Max seconds to spawn a mkosi VM.            |
| `build`     | int | `--mkosi.build`, `-f` | Build the mkosi image before spawning VMs, may specify multiple times -ff for different mkosi force levels. |

### `[custom_vm]` Section

| Option      | Type      | CLI Argument(s)         | Description                                 |
|-------------|-----------|-------------------------|---------------------------------------------|
| `vms`       | list[str] | `--vms`       | List of `HOST:PATH` pairs (e.g., `vm1:/fstests,vm2:/home/fstests`). |

### `[output]` Section
| Option         | Type      | CLI Argument(s)         | Description                                 |
|----------------|-----------|-------------------------|---------------------------------------------|
| `results_dir`  | Path      | `--results-dir`         | Path to store test results.                 |
| `print_failure_list` | bool | `--print-failure-list` | Print list of tests that failed in pasteable format. |
| `print_n_slowest` | int | `--print-n-slowest` | Print n slowest tests and their times. |
| `print_duration_hist` | bool | `--print-duration-hist` | Print histogram of test durations. (optional dependency required: plotext) |

## run
```
fast-fstests -n 5 -g btrfs/auto
```

# Configuring Mkosi
Here are directions for getting everything setup with mkosi.
Caveat this guide is not intended as a comprehensive mkosi setup guide,
it is only detailing some additional configuration needed to get mkosi working with fast-fstests.
It is highly recommended that before starting this guide you already have a working
mkosi and mkosi-kernel setup that can run fstests.

1. mkosi-kernel profile\
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
cd fstests
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

6. If all steps above are working you should be good to go!
```
fast-fstests --mkosi 5 --group btrfs/auto
```
