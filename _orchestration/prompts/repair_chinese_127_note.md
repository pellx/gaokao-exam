你是语文单卷定点返修子代理。只处理 E:/gaokao-exam/parsed/2026/二模/西城/语文/paper.json，entry_key 为 2026|二模|西城|语文|。
先读 data/question_extraction/2026/chinese/127/review_5_result.json。该报告已经逐页核对全卷，唯一 reject 是第20题 notes[1] 把第③句下划线范围写宽。
只允许将 questions[19].notes[1] 改为：第③句原卷下划线覆盖“意味着模型被使用得越多，创造的实际价值也越大”。
不要改该题正文、任何答案或其他 notes，不要改变 extraction.status，不要改原始附件、全局状态、脚本、Schema 或其他文件。不要重新提取整卷。必要时读取原始 PDF 第8页及其放大图核对起点；禁止联网、解题及创建其他子代理。Python 使用 python -I。
执行 python -I scripts/validate_exam_paper.py "parsed/2026/二模/西城/语文/paper.json" --schema docs/question-json-v1.schema.json。简洁返回实际修改字段和校验结果，不得自标 verified。
