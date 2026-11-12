"""Chạy bản agent được lưu trước vòng so sánh đủ 22 case; giữ nguyên runner/judge."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))

import run_evals
from agent_before import run_agent

if __name__ == '__main__':
    run_evals.run_agent = run_agent
    sys.exit(run_evals.main())
