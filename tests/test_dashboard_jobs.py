import io
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from whitespace.serve.jobs import JobRunner, commands, parse_appids


def test_appid_parsing_and_validation():
    assert parse_appids("413150, 1145360 413150") == [413150, 1145360]
    for invalid in ("", "0", "12;rm", "-1", "1," * 101):
        with pytest.raises(ValueError):
            parse_appids(invalid)


def test_pipeline_commands_and_validation():
    stages = commands("pipeline", "steam_reviews", "azure", 20)
    assert [stage[0] for stage in stages] == ["ingest", "clean", "extract", "catalog", "context"]
    assert stages[2] == ["extract", "--extractor", "azure", "--limit", "20"]
    with pytest.raises(ValueError):
        commands("extract", "steam_reviews", "azure", 0)
    with pytest.raises(ValueError):
        commands("unknown", "steam_reviews", "azure", 20)


def test_job_uses_temporary_config_and_stops_after_failure(monkeypatch):
    calls = []

    class Process:
        def __init__(self, args, **kwargs):
            config = yaml.safe_load(Path(args[args.index("--config") + 1]).read_text())
            calls.append((args, kwargs, config))
            self.stdout = io.StringIO("work output\n")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            return 1 if len(calls) == 2 else 0

    monkeypatch.setattr("whitespace.serve.jobs.subprocess.Popen", Process)
    runner = JobRunner()
    stages = commands("pipeline", "steam_reviews", "azure", 10)
    runner._run(stages, [123, 456], {"AZURE_OPENAI_API_KEY": "secret"})
    status, output = runner.snapshot()
    assert status == "failed" and "remaining stages were not run" in output
    assert len(calls) == 2
    assert calls[0][2]["sources"]["steam_reviews"]["appids"] == [123, 456]
    assert calls[0][1]["env"]["AZURE_OPENAI_API_KEY"] == "secret"
    assert "secret" not in output
    assert not Path(calls[0][0][calls[0][0].index("--config") + 1]).exists()


def test_only_one_job_can_run(monkeypatch):
    threads = []

    class Thread:
        def __init__(self, **kwargs):
            threads.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr("whitespace.serve.jobs.threading.Thread", Thread)
    runner = JobRunner()
    assert runner.start([["clean"]], [123], {})
    assert not runner.start([["clean"]], [123], {})
    assert len(threads) == 1 and runner.snapshot()[0] == "running"


def test_stop_terminates_current_stage_and_skips_remaining(monkeypatch):
    runner = JobRunner()
    runner._status = "running"
    launched = []

    class Output:
        def __iter__(self):
            yield "working\n"
            assert runner.stop()

    class Process:
        def __init__(self, args, **kwargs):
            launched.append(self)
            self.stdout = Output()
            self.terminated = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return -15 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self):
            return -15

    monkeypatch.setattr("whitespace.serve.jobs.subprocess.Popen", Process)
    runner._run([["clean"], ["extract"]], [123], {})
    assert len(launched) == 1 and launched[0].terminated
    assert runner.snapshot()[0] == "stopped"
    assert not runner.stop()


def test_stop_between_stages_does_not_start_another(monkeypatch):
    runner = JobRunner()
    runner._status = "running"
    launched = []

    class Process:
        def __init__(self, args, **kwargs):
            launched.append(args)
            self.stdout = io.StringIO("done\n")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return 0

        def wait(self):
            assert runner.stop()
            return 0

    monkeypatch.setattr("whitespace.serve.jobs.subprocess.Popen", Process)
    runner._run([["clean"], ["extract"]], [123], {})
    assert len(launched) == 1 and runner.snapshot()[0] == "stopped"


def test_stop_terminates_a_real_running_process(monkeypatch):
    runner = JobRunner()
    launched, finished = threading.Event(), threading.Event()
    popen, run = subprocess.Popen, runner._run

    def start_process(args, **kwargs):
        process = popen([sys.executable, "-u", "-c", "import time; time.sleep(60)"], **kwargs)
        launched.set()
        return process

    def run_and_signal(*args):
        try:
            run(*args)
        finally:
            finished.set()

    monkeypatch.setattr("whitespace.serve.jobs.subprocess.Popen", start_process)
    monkeypatch.setattr(runner, "_run", run_and_signal)
    assert runner.start([["clean"], ["extract"]], [123], os.environ.copy())
    assert launched.wait(10)
    assert runner.stop()
    assert finished.wait(10)
    assert runner.snapshot()[0] == "stopped"


def test_real_clean_stage_runs_in_subprocess(tmp_path):
    runner = JobRunner()
    runner._run([["clean"]], [413150], {**os.environ, "WHITESPACE_DATA_DIR": str(tmp_path)})
    status, output = runner.snapshot()
    assert status == "completed" and "> whitespace clean" in output
    assert (tmp_path / "whitespace.sqlite").exists()
