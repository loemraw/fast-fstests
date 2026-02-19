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
# or if you want plots in output
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
| `slowest_first`     | str/int      | `--slowest-first [SOURCE]`      | Sort tests slowest-first using duration data from SOURCE. Uses the most recent run if omitted. Requires `--results-dir`. |
| `rerun_failures`    | str/int      | `--rerun-failures [SOURCE]`     | Re-run only failed/errored tests from SOURCE. Uses latest if omitted. Requires `--results-dir`. |

### `[test_runner]` Section
| Option      | Type      | CLI Argument(s)         | Description                                 |
|-------------|-----------|-------------------------|---------------------------------------------|
| `keep_alive`   | bool      | `--keep-alive`, `--no-keep-alive`          | Keep hosts alive for debugging.             |
| `test_timeout` | int | `--test-timeout` | Max seconds to run a test. |
| `bpftrace` | str | `--bpftrace` | BPFTrace script to be executed with -e |
| `bpftrace_script` | Path | `--bpftrace-script` | BPFTrace script path that will be executed on vm |
| `probe_interval` | int | `--probe-interval` | Seconds between liveness probes (0 to disable). Default: 30. |
| `max_supervisor_restarts` | int | `--max-supervisor-restarts` | Max times a test can kill a supervisor before being marked as error. Default: 3. |
| `dmesg` | bool | `--dmesg`, `--no-dmesg` | Stream dmesg output during test execution. Default: true. |
| `retry_failures` | int | `--retry-failures` | Max times to retry a failed test before recording failure (0 to disable). Default: 0. |

### `[mkosi]` Section

| Option      | Type      | CLI Argument(s)         | Description                                 |
|-------------|-----------|-------------------------|---------------------------------------------|
| `num`       | int       | `--mkosi.num`, `-n`     | Number of mkosi VMs to spawn.               |
| `config`    | Path      | `--mkosi.config`        | **Required if using mkosi** Path to mkosi config.  |
| `options`   | list[str] | `--mkosi.options`       | List of options for mkosi.                  |
| `include`   | Path      | `--mkosi.include`       | Path to mkosi config to pass through to mkosi. |
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
| `verbose`      | bool      | `--verbose`, `-v`       | Print debugging logs to RESULTS_DIR/log. Requires `--results-dir`. |
| `print_failure_list` | bool | `--print-failure-list` | Print list of tests that failed in pasteable format. |
| `print_n_slowest` | int | `--print-n-slowest` | Print n slowest tests and their times. |
| `print_duration_hist` | bool | `--print-duration-hist` | Print histogram of test durations. (optional dependency required: plotext) |
| `record` | str | `--record [LABEL]` | Record this run for future comparisons. Labels with timestamp if omitted. |

## Recordings & Comparisons

### Recording runs

Use `--record` to save a snapshot of test results for later comparison. The label defaults to the current timestamp if omitted.

```bash
# Record with an explicit label
ff --record before-fix -g btrfs/auto

# Record with auto-generated timestamp label
ff --record -g btrfs/auto
```

Forgot to pass `--record`? Use `ff record` after the run to retroactively save it:

```bash
ff -g btrfs/auto
# oops, forgot --record
ff record my-label

# Or with auto-generated timestamp label
ff record
```

Recordings are stored as directory symlinks in `results/recordings/{label}/` and are never deleted.

### Comparing runs

Use `ff compare` to diff two runs. Each source (`--baseline`/`-a`, `--changed`/`-b`) can be:

| SOURCE | Meaning |
|--------|---------|
| *(omitted)* | The most recent run (from `results/latest/`) |
| `my-label` | A named recording |
| `-1` | Most recent recording (by time) |
| `-2` | Second most recent recording |

```bash
# Compare the two most recent recordings (default: --baseline -2 --changed -1)
ff compare

# Compare a named recording against the most recent run
ff compare --baseline before-fix --changed

# Compare two named recordings
ff compare -a before-fix -b after-fix

# Compare the most recent run against the most recent recording
ff compare --baseline --changed -1
```

### Duration-aware scheduling

Use `--slowest-first` to sort tests by duration from a previous run so the longest tests start first across VMs, minimizing total wallclock time. SOURCE follows the same format as `ff compare`:

```bash
# Use durations from the most recent run
ff --slowest-first -g btrfs/auto

# Use durations from a specific recording
ff --slowest-first before-fix -g btrfs/auto

# Use durations from the most recent recording
ff --slowest-first -1 -g btrfs/auto
```

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
fast-fstests -n 5 -g btrfs/auto
```
