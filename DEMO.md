# Demo agent từ kết quả thật

Chạy tại gốc repo. Các lệnh dưới chỉ đọc JSON, không cần key và không gọi LLM.

## 1. Trace nhiều bước và theo liên kết

```powershell
python demo_trace.py --report eval_after.json --case H1
```

Bước 1 đọc incident GitHub được hỏi và incident Cloudflare. Bước 2 đọc incident
GitHub được dẫn tới từ update. Kiểm tra ID, provider, snapshot timestamp và URL
trước khi đọc kết luận. Trace ghi thao tác, không phải suy nghĩ nội bộ của model.

## 2. Một lỗi điều phối và kết quả cải tiến

```powershell
python demo_trace.py --report eval_before.json --case H6
python demo_trace.py --report eval_after.json --case H6
```

Trước: đã đọc đúng h02n9572mx9y và xr3myzj7ms51, nhưng validation ở bước 4 lại
ép đọc t89g2q1800fr vì khớp tên incident lịch sử. Final đổi trọng tâm sang ID này,
được 15 điểm dù status completed. Sau: trả đúng câu hỏi ban đầu và được 100.
Xem observations đầy đủ trong JSON để kiểm chứng nguyên nhân và cờ truncated.

## 3. Trình bày cả lỗi còn lại

```powershell
python demo_trace.py --report eval_after.json --case H2
python demo_trace.py --report eval_after.json --case H8
```

H2 nói cần get_status nhưng chưa gọi. H8 bị feedback kéo sang hai incident Cloudflare,
bỏ phần GitHub và giới hạn dữ liệu lịch sử. Cả hai được 50 điểm: aggregate tăng
không đồng nghĩa mọi case đều tốt hơn. NOTES.md giải thích bằng chứng và hướng sửa.

## 4. Bonus: pagination và retry có giới hạn

```powershell
python demo_trace.py --report eval_bonus.json --case B2
python demo_trace.py --report eval_bonus.json --case B4
```

B2 đọc offset 0, 3, 6 để tìm update liên quan. B4 dừng Vercel sau 3 lần rate limit,
vẫn báo được phần GitHub. B6 và B10 còn thiếu bước đọc chi tiết/retry; xem report.

## 5. Kiểm tra và nộp bài

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s bonus -p 'test_*.py' -v
```

So sánh config của eval_before.json / eval_after.json, đủ 22/22 case và không unscored.
Bonus có 10/10 case, được chấm riêng. Xem eval_manifest.json về hash và metrics.
Làm checklist cá nhân trong SUBMISSION.md; không đưa .env lên repo.
