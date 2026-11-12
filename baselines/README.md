# Baseline của vòng đánh giá đầy đủ

`agent_before.py` là bản sao nguyên vẹn của `agent.py` ngay trước khi chạy bộ 22 case
trong `eval_before.json`. Bản này đã có cải tiến của lượt E2 trước đây; không phải
bản agent ban đầu của đề bài hay một baseline được suy dựng từ kết quả.

SHA-256: `d813d0dba425de7874ecfccb18c22a225791e34b4623a2d330d42fd1255cdd87`.

Trong khi tiến trình baseline chạy, Python giữ module đã import trong bộ nhớ;
các sửa đổi tiếp theo trên `agent.py` chỉ áp dụng cho tiến trình eval sau.

Có thể chạy lại baseline từ gốc repo mà không ghi đè agent hiện tại:

```powershell
$env:PROVIDER_RATE_RETRIES='4'
python baselines/run_before.py --provider gemini --model gemini-3.5-flash-lite --judge-provider gemini --judge-model gemini-3.5-flash-lite --max-steps 8 --workers 1 --output eval_before.json
```

Script chỉ thay hàm agent mà runner gọi; giữ nguyên câu hỏi, scenario, tools, judge,
expected và cách tính điểm. API key được đọc từ `.env`, không lưu trong baseline.
