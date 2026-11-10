---
title: Workforce Assistant API
emoji: 📊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

Back end for the Workforce Assistant demo: a FastAPI service that answers leaders'
questions about their own part of an organisation, with access scoping, small-group
suppression and answer verification.

Source, tests and evaluation report: https://github.com/BinuShefieldShifani/workforce-assistant

Data: the IBM HR Analytics Employee Attrition & Performance sample (fictional, 1,470
employees), from https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset,
with a generated org chart.

Endpoints: `GET /api/health`, `GET /api/personas`, `POST /api/ask`, `POST /api/feedback`.
