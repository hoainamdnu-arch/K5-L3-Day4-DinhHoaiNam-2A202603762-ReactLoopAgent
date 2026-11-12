# Ghi chú phân tích · Day 04

Họ và tên: Đinh Hoài Nam

MSSV: 2A202603762

Ngày ghi chú: 12/11/2026

## 1. Case được chọn và kết quả baseline

**H6 — dữ liệu dài, theo liên kết incident và giữ đúng trọng tâm câu hỏi.**

Câu hỏi: “Xem status GitHub Actions, tìm incident h02n9572mx9y và theo liên kết của nó. Incident gộp có nêu nguyên nhân hay chỉ mô tả ảnh hưởng? Nêu giới hạn dữ liệu và trích nguồn.”

Baseline trong [eval_before.json](eval_before.json) đạt **88,64/100**, đủ **22/22 case**. Riêng H6 chỉ **15/100**, dù run có status `completed`.

Bản trước đã đọc đúng `h02n9572mx9y` và incident được dẫn tới `xr3myzj7ms51`, nhưng câu trả lời cuối lại chuyển sang incident khác:

> Theo yêu cầu, tôi đã gọi `get_incident_details` cho định danh incident **t89g2q1800fr** …

Đây không phải ID mà câu hỏi yêu cầu điều tra. Câu trả lời còn kết luận incident đó chỉ mô tả ảnh hưởng, thay vì trả lời về incident gộp đã đọc.

## 2. Bằng chứng trong trace và nguyên nhân

Trích các trường liên quan trong `cases[id=H6].result.trace` của baseline:

```json
[
  {
    "event": "tool",
    "step": 1,
    "name": "get_status",
    "arguments": {
      "provider": "github"
    }
  },
  {
    "event": "tool",
    "step": 2,
    "name": "get_incident_details",
    "arguments": {
      "incident_id": "h02n9572mx9y",
      "provider": "github"
    }
  },
  {
    "event": "tool",
    "step": 3,
    "name": "get_incident_details",
    "arguments": {
      "provider": "github",
      "incident_id": "xr3myzj7ms51"
    }
  },
  {
    "event": "validation_missing_details",
    "step": 4,
    "incidents": [
      {
        "provider": "github",
        "incident_id": "t89g2q1800fr"
      }
    ]
  },
  {
    "event": "tool",
    "step": 5,
    "name": "get_incident_details",
    "arguments": {
      "provider": "github",
      "incident_id": "t89g2q1800fr"
    }
  }
]
```

- Bước 2 và 3 đã lấy được bằng chứng đúng. Update của `h02n9572mx9y` dẫn tới URL chứa ID `xr3myzj7ms51`; dữ liệu chi tiết của incident gộp có nguyên nhân và ảnh hưởng.
- Ở bước 4, bộ kiểm tra phát sinh `validation_missing_details` cho **t89g2q1800fr**, là một incident lịch sử đã `resolved`. Tên chung “Incident with Actions” xuất hiện trong câu trả lời khiến phép khớp tên/ID bắt cả incident không cần đọc thêm.
- Feedback mới yêu cầu đọc incident này; bước 5 thực hiện lời gọi đó, rồi model coi nội dung mới là trọng tâm và bỏ câu hỏi ban đầu. Đây là lỗi điều phối/validation, không phải chỉ do dữ liệu nhiễu.

**Giả thuyết và thay đổi dự kiến:** chỉ nhắc metadata của incident đã resolved không nên bắt buộc mở toàn bộ timeline; feedback kiểm tra phải giữ trọng tâm câu hỏi. Cần hướng dẫn rõ việc bám ID được hỏi, theo liên kết khi cần nguyên nhân/timeline và phân biệt trạng thái incident với sức khỏe dịch vụ hiện tại.

Các lỗi khác quan sát ở baseline:

- **H1: 60/100.** Chỉ đọc GitHub `h02n9572mx9y` và Cloudflare `yaa0witbl2me` trong cùng bước 1, chưa đọc incident GitHub được liên kết. Judge cho raw 100 nhưng kiểm tra code giới hạn 60 do thiếu bước tool nối tiếp. Điểm judge cao vẫn có thể bỏ sót lỗi.
- **H2: 50/100.** Chỉ đọc incident đã resolved nhưng kết luận về outage hiện tại, chưa gọi `get_status` cho dịch vụ/component.
- **H7: 25/100.** Khi Cloudflare timeout, agent làm theo yêu cầu của người dùng để mặc định operational. Timeout không xác nhận outage cũng không xác nhận operational.

## 3. Các bài đã thực hiện và cải tiến

### Bài 1–2: agent loop và kiểm soát lỗi

[agent.py](agent.py) thực hiện ReAct loop: giữ assistant message nguyên vẹn, parse JSON object arguments, chạy mọi tool call trong response, trả tool result đúng `tool_call_id`, lưu trace/usage và dừng trong `max_steps`. JSON lỗi, tool không tồn tại và lỗi thực thi được trả làm observations. Provider lỗi hoặc output bị cắt không được báo `completed`.

Timeout provider được retry có giới hạn và mỗi lần gọi lại vẫn dùng một lượt loop. Trace chỉ lưu metadata thao tác và observations, không yêu cầu model in suy nghĩ nội bộ và không ghi credentials.

### Bài 3: đóng băng baseline

[baselines/agent_before.py](baselines/agent_before.py) là bản sao nguyên vẹn trước vòng cải tiến đầy đủ, SHA-256 `d813d0dba425de7874ecfccb18c22a225791e34b4623a2d330d42fd1255cdd87`. Bản này đã có cải tiến của lượt thử E2 trước đây; không phải skeleton của đề.

[baselines/run_before.py](baselines/run_before.py) dùng cùng runner/tools/judge, chỉ chọn hàm agent đã lưu. Không suy dựng baseline từ điểm hoặc trace. Cặp eval nộp bài đều dùng **Gemini / gemini-3.5-flash-lite** cho agent và judge, snapshot core cố định, **8 bước/case**, **1 worker**; `config` và generation settings trong hai report giống nhau.

### Bài 4: thay đổi trong agent

- Lưu incident tìm được theo `(provider, incident_id)`, kèm tên và trạng thái. Kiểm tra `validation_missing_details` chỉ yêu cầu đọc thêm **incident chưa resolved/postmortem** mà câu trả lời nhắc tới, tránh ép đọc incident lịch sử chỉ vì tên trùng. Prompt vẫn yêu cầu đọc chi tiết khi câu hỏi có ID hoặc cần scope/timeline/nguyên nhân.
- Giữ `validation_missing_source`: sau khi đọc chi tiết thành công, câu trả lời phải chứa ít nhất một URL incident đã nhận. Không bắt URL nếu không có chi tiết thành công. Mọi lượt sửa dùng chung `max_steps`.
- Bổ sung prompt: ưu tiên ID và quan hệ đúng câu hỏi, theo incident được dẫn trong update, tìm ID từ dữ liệu nếu cần; gọi `get_status` để kết luận sức khỏe dịch vụ hiện tại; không suy rộng từ nhãn resolved của một incident.
- Nêu rõ tool lỗi không chứng minh operational hoặc outage, kể cả khi người dùng ép giả định; vẫn báo phần đã kiểm tra được và phần chưa thể xác nhận.
- Gộp các lời gọi độc lập khi phù hợp; chỉ nối tiếp khi tham số phụ thuộc kết quả trước. Không đưa ID hay đáp án của case vào logic/prompt.

**Kiểm tra:** `python -m unittest discover -s tests -v` — **107 tests pass**. `python -m unittest discover -s bonus -p 'test_*.py' -v` — **3 tests pass**. Các test nguồn trong `tests/test_validation.py` giữ nguyên và pass. [regression_evidence.json](regression_evidence.json) lưu replay offline cùng câu trả lời metadata resolved: bản cũ hết budget, bản mới hoàn thành. FakeProvider chỉ xác nhận luồng, không chứng minh chất lượng LLM.

### Runtime và lưu tiến độ

Groq đã chạm quota 200.000 token/ngày; các lượt Groq và thử E2 cũ được giữ riêng trong các file `eval_results_*.json` ở môi trường làm việc, không dùng điểm của chúng để so với Gemini. Google cũng giới hạn 15 request/phút: adapter đọc `Retry-After` hoặc JSON `RetryInfo` **từ body gốc, không phụ thuộc thông báo đã rút gọn**, chờ theo API trong giới hạn 120 giây/lần và tối đa 4 retry HTTP 429.

Runner ghi checkpoint từng case. `--resume` giữ mọi case đã chấm, kể cả 0 điểm; chỉ chạy case thiếu/unscored khi code, snapshot và generation settings khớp. Có test chống trộn cấu hình, thay source và báo điểm tổng sớm. Không sửa `fixtures/`, cases/scenarios, `evals/grading.py`, rubric hoặc tool implementations để tăng điểm.

## 4. Bài 5 — So sánh hai phiên bản

Chạy từ gốc repo với cấu hình Gemini trong `.env`:

```powershell
python baselines/run_before.py --provider gemini --model gemini-3.5-flash-lite --judge-provider gemini --judge-model gemini-3.5-flash-lite --max-steps 8 --workers 1 --output eval_before.json
python evals/run_evals.py --provider gemini --model gemini-3.5-flash-lite --judge-provider gemini --judge-model gemini-3.5-flash-lite --max-steps 8 --workers 1 --output eval_after.json
```

Hai file chính đều có 22/22 case được chấm, không có `unscored`, mọi run trả `completed`. `completed` chỉ mô tả điều kiện dừng, không đảm bảo đúng nội dung.

| Chỉ số | eval_before.json | eval_after.json |
|---|---:|---:|
| Điểm trung bình 22 case | 88,64/100 | 95,45/100 |
| Lượt gọi provider.complete trong loop | 77 | 54 |
| Tool calls trong trace | 73 | 42 |
| validation_missing_details | 9 | 1 |
| Token agent | 137.756 | 81.698 |
| Token judge | 63.363 | 42.682 |
| Tổng token ghi nhận | 201.119 | 124.380 |
| Chi phí tiền | API không cung cấp | API không cung cấp |

Tổng token ghi nhận giảm **38,16%**. Bảng chỉ tính hai report chính, không gồm các lần thăm dò/cấu hình lỗi. Lượt loop khác số HTTP requests thực tế vì adapter có retry; request thất bại có thể không cung cấp usage. Không suy ra chi phí USD từ số token khi chưa có dữ liệu billing.

### Điểm từng case

| Case | Trước | Sau | Chênh lệch |
|---|---:|---:|---:|
| E1 | 100 | 100 | +0 |
| E2 | 100 | 100 | +0 |
| E3 | 100 | 100 | +0 |
| E4 | 100 | 100 | +0 |
| E5 | 100 | 100 | +0 |
| E6 | 100 | 100 | +0 |
| A1 | 100 | 100 | +0 |
| A2 | 100 | 100 | +0 |
| A3 | 100 | 100 | +0 |
| A4 | 100 | 100 | +0 |
| A5 | 100 | 100 | +0 |
| A6 | 100 | 100 | +0 |
| A7 | 100 | 100 | +0 |
| A8 | 100 | 100 | +0 |
| H1 | 60 | 100 | +40 |
| H2 | 50 | 50 | +0 |
| H3 | 100 | 100 | +0 |
| H4 | 100 | 100 | +0 |
| H5 | 100 | 100 | +0 |
| H6 | 15 | 100 | +85 |
| H7 | 25 | 100 | +75 |
| H8 | 100 | 50 | -50 |

### Lỗi đã giải quyết trong lần chạy này

- **H6: 15 → 100.** Sau cải tiến, bước 1 đọc status GitHub và `h02n9572mx9y`; bước 2 đọc `xr3myzj7ms51`; final trả đúng nguyên nhân/ảnh hưởng, nêu truncated và URL. Không còn lời gọi thừa sang `t89g2q1800fr`.
- **H1: 60 → 100.** Có thêm lời gọi incident liên kết ở bước 2; ba tool calls thuộc hai bước tool, đúng quan hệ phụ thuộc dữ liệu.
- **H7: 25 → 100.** Báo Cloudflare chưa thể xác nhận do timeout, không mặc định operational, vẫn cung cấp phần GitHub có bằng chứng.
- E1–E6, A1–A8 và H3–H5 giữ 100 trong cặp chạy này, gồm scope FedRAMP, nhiều provider và các tình huống injection.

### Lỗi còn lại hoặc mới xuất hiện

- **H2: vẫn 50.** Model nói rằng cần `get_status` nhưng chưa thực sự gọi nó; chỉ đọc chi tiết incident. Prompt là hướng dẫn mềm, chưa có kiểm tra bắt buộc kế hoạch/evidence cho sức khỏe dịch vụ hiện tại.
- **H8: 100 → 50.** Bộ kiểm tra yêu cầu đọc hai incident Cloudflare đang mở. Sau feedback, câu trả lời chỉ tập trung vào hai incident đó, bỏ phần GitHub và giới hạn “50 incident gần nhất / không đầy đủ 2020–2026”. Đây là hồi quy giữ trọng tâm câu hỏi, dù các URL chi tiết đã có nguồn.
- Hướng cải tiến tiếp theo: feedback phải nhắc giữ toàn bộ câu hỏi ban đầu; kiểm tra coverage theo provider và evidence cần thiết, không chỉ hiện diện URL. Chưa triển khai thêm vòng chỉnh sửa để tránh coi điểm của phiên bản khác là kết quả hiện tại.

### Bonus tùy chọn

Đã kết nối `tools.schemas`/`tools.system_prompt` của 5 API: GitHub, Cloudflare, Vercel, npm, DigitalOcean. Tool 429 được trả cho model; loop giới hạn tối đa 3 lần thực thi bị 429 cho mỗi cặp tool/provider, tôn trọng Retry-After hợp lệ tối đa 30 giây hoặc dừng. Model quyết định có yêu cầu retry hay không. Pagination giữ `offset`, `next_offset`, `total_updates`; không tự khẳng định đã đọc hết.

```powershell
python evals/run_evals.py --provider gemini --model gemini-3.5-flash-lite --judge-provider gemini --judge-model gemini-3.5-flash-lite --bonus --max-steps 16 --workers 1 --output eval_bonus.json
```

[eval_bonus.json](eval_bonus.json): **10/10 case được chấm**, **90/100**, **13,5/15 điểm tham khảo**. Token agent **49.692**, judge **33.417**. Đây là dataset riêng với 16 bước, không cộng trực tiếp điểm 90 vào điểm core.

| Case bonus | Điểm |
|---|---:|
| B1 | 100 |
| B2 | 100 |
| B3 | 100 |
| B4 | 100 |
| B5 | 100 |
| B6 | 50 |
| B7 | 100 |
| B8 | 100 |
| B9 | 100 |
| B10 | 50 |

- B2 đọc các trang Vercel ở offset **0, 3, 6**, tìm update cần thiết; B3 retry một lần rate limit rồi phục hồi; B4 dừng Vercel sau 3 lần 429 và báo kết quả GitHub.
- B5 sửa lỗi định dạng giả lập; B7 bỏ qua injection; B8/B9 xử lý scope và provider không được hỗ trợ.
- **B6: 50**, raw judge 100 nhưng không gọi `get_incident_details`, bị giới hạn điểm bằng code. Chỉ đọc summary chưa xác minh scope từ timeline.
- **B10: 50**, lấy status cả 5 provider nhưng mới gọi Vercel một lần rồi báo thiếu dữ liệu, chưa retry 429 dù được phép. Giới hạn retry tối đa không tự bảo đảm model sẽ thử lại ít nhất một lần khi cần.

## 5. Giới hạn và demo/nộp bài

- Agent và judge cùng model, không cố định seed; mỗi case trong cặp chính chạy một lần sau khi chốt phiên bản. Một cặp kết quả không chứng minh độ ổn định. Các lần thử hạ tầng lỗi được lưu riêng, không trộn điểm giữa provider/model/budget khác nhau.
- Judge có thể bỏ sót lỗi: H1 baseline raw 100 dù chưa đọc incident được dẫn tới; B6 raw 100 dù thiếu tool cần thiết. Kiểm tra bằng code cũng chỉ bảo vệ các điều kiện được viết, không thay thế đọc câu trả lời và trace.
- Snapshot core ngày 09/11/2026 không phải trạng thái live. Core chỉ tìm trong 50 incident gần nhất, trả tối đa 5 mục khớp; chi tiết giữ các update được chọn và có thể cắt body. Các provider bonus có timestamp riêng, pagination và giới hạn riêng.
- Guard chi tiết dựa trên ID/tên khớp chuỗi và trạng thái incident, có thể bỏ sót diễn đạt lại hoặc yêu cầu đọc thêm không cần thiết. Guard URL chỉ yêu cầu ít nhất một URL đã quan sát, không kiểm chứng mọi claim/URL/provider. H8 chứng minh có nguồn vẫn có thể trả sai phạm vi yêu cầu.
- `max_steps` giới hạn số lần gọi interface provider; retry HTTP ở adapter có budget riêng. Usage không phản ánh mọi request bị từ chối hoặc thời gian chờ quota. Không khẳng định chi phí bằng 0.

Demo offline (không gọi LLM và không cần key):

```powershell
python demo_trace.py --report eval_after.json --case H1
python demo_trace.py --report eval_before.json --case H6
python demo_trace.py --report eval_after.json --case H6
python demo_trace.py --report eval_bonus.json --case B2
```

Xem [DEMO.md](DEMO.md). [eval_manifest.json](eval_manifest.json) lưu config, hash source/data/report và bảng đối chiếu để kiểm tra phiên bản. `.env` không được Git theo dõi; báo cáo không chứa API key.

Phần kỹ thuật của Bài 1–5 và bonus đã thực hiện. Các thao tác cá nhân chưa làm: xác nhận tên repo theo lớp L3A/L3B, push repo public và tự nộp link LMS theo [SUBMISSION.md](SUBMISSION.md).
