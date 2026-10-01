from pathlib import Path

LANGUAGES = ("python", "java", "cpp")
ALPHA = 0.01
MIN_EVIDENCE = 5
N_TESTS = 2
FIXPOINT_MAX_ITER = 4
Q_TABLE_PATH = Path(__file__).resolve().parent / "q_table.json"
EXPERIMENT_KEY = bytes.fromhex("76" * 16)
