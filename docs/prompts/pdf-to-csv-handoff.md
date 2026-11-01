# PDF → CSV handoff

Companion repository: `PrameKung/capstone-reviewer`.
Use its `docs/prompts/pdf-to-training-handoff.md` for the complete copyable Thai
prompt, including source review, explicitly inferred training data and A/B groups.
The paired handoff branches are:

- pipeline: `feature/ocr-evidence-handoff-20261101`
- reviewer: `feature/review-training-handoff-20261101`

For an OCR-only task, copy this prompt and replace the paths:

```text
ทำ PDF → CSV ด้วย capstone-pipeline ในเครื่องนี้
REPO=<repository path>
RAW=<student PDFs directory>
QUESTION_PDF=<clean questions PDF or none>
RUBRIC=<official rubric or none>
OUTPUT=<new output directory>

อ่าน AGENTS.md, README, .env.example และ docs/csv-output-format.md ก่อน
สำรวจไฟล์/หน้า/metadata ตรวจข้อสอบตรงชุด และเก็บ hash ต้นฉบับ
ตั้ง .env ตามเครื่องจริง ไม่แสดง credentials ใช้ Codex CLI ที่ล็อกอิน ChatGPT
เริ่ม pilot 1 PDF ตามคำสั่ง worker ที่ README รองรับ แล้วตรวจผลก่อนขยาย concurrency
ใช้สำเนาปิดครึ่งบนหน้าแรกอย่างถาวร เก็บเลขหน้าต้นฉบับ ไม่กู้ส่วนที่ปิด
ถอดคำตอบตามภาพ รักษาคำตอบผิด งานขีดฆ่า กราฟ ข้อย่อยและหน้าต่อเนื่อง
ถ้าไม่ชัดใช้ [unclear] ตาม parser ไม่ใช้เฉลยเดา ไม่ใส่คะแนน/คำแก้ของครูเป็นคำตอบนักศึกษา
อ่านคะแนนแยก pass ไม่ regrade; คะแนนไม่ชัดให้ว่าง ไม่ใช่ 0
เก็บ unknown mapping ไม่ทิ้งแถว และไม่คัดลอกบริเวณเดียวกันซ้ำในข้อแม่/ข้อย่อย
ใช้ student prompt OCR v13 ตาม extract.py; self-check นี้ไม่ใช่ independent audit
ส่ง CSV 38 ฟิลด์ตาม schema เดิม เก็บ artifact/checkpoint ใน .pipeline
ตรวจ schema, จำนวน source rows, ID และหลักฐานคะแนนก่อนส่งมอบ
งาน OCR ห้ามสร้างคำตอบสังเคราะห์หรือให้คะแนน AI แทนอาจารย์
ถ้าต้องการ AI review/ชุดฝึกเพิ่มเติม ให้ทำตาม companion handoff แยกเป็นอีกขั้น
สรุปไฟล์ที่สร้าง ข้อที่ยังไม่ชัด และสิ่งที่ทดสอบจริง ไม่อ้าง accuracy ที่ไม่ได้วัด
```

This branch changes the student prompt and OCR cache version only. It does not
add an independent audit controller, a new document-map schema, image crops,
physical teacher-mark removal, or a new training export endpoint.
Normal OCR reruns invalidate pre-v13 caches; export-only reuses saved OCR.
Rebuild/recreate deployed workers before expecting the new prompt to take effect.
