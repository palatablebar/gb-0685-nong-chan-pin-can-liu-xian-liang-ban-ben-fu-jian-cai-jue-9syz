TITLE='农产品残留限量版本与复检裁决'
SHORT='residuelimit'
RULE_VERSION='2026.11.17-v1'
MODULES=[('m1_ingest', '报告采信'), ('m2_mapping', '项目映射'), ('m3_versions', '限量选版'), ('m4_segments', '检出量换算'), ('m5_calculate', '复检裁决'), ('m6_reconcile', '批次判级'), ('m7_archive', '报告归档')]
# 补正裁决是工作台模块，不参与 M1–M7 顺次管线，单独挂载并追加到导航。
EXTRA_MODULES=[('m8_amend', '补正裁决')]
REQUIRED=['id', 'sample_id', 'batch', 'product', 'analyte', 'method', 'version', 'sampled', 'reported', 'result', 'unit', 'lod']
NUMERIC=['version', 'lod']
DATES=['sampled', 'reported']
TIMES=[]
