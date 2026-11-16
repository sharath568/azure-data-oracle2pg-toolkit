#!/usr/bin/env bash
# Run the hard corpus (tests/fixtures/ai) and the bundled sample through real
# Azure OpenAI deployments, keeping every answer so the run can be replayed.
#
#   export AZURE_OPENAI_ENDPOINT=https://<name>.openai.azure.com
#   export AZURE_OPENAI_API_KEY=...            # or AZURE_OPENAI_AD_TOKEN
#   docker run -d --name o2pg-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
#   scripts/ai_eval.sh gpt-5-mini gpt-4.1-mini  # deployment names; default gpt-5-mini
#
# Output per deployment, under eval/<deployment>/:
#   corpus/ai-report.md      gates and Oracle equivalence for the 13 hard units
#   corpus-answers/          raw answers (replay with --provider replay --replay ...)
#   sample/ai-report.md      the bundled HR/SALES sample: compile gate only, no cases
#   sample-answers/
#
# Cost: each unit is one or two calls of a few thousand tokens. The report prints
# the token count and an estimate at list price.
set -euo pipefail

cd "$(dirname "$0")/.."
DSN="${O2PG_AI_DSN:-postgresql://postgres:postgres@localhost:5432/postgres}"
if [ "$#" -eq 0 ]; then
  set -- "${AZURE_OPENAI_CHAT_DEPLOYMENT:-gpt-5-mini}"
fi

for deployment in "$@"; do
  out="eval/${deployment}"
  rm -rf "${out}"
  echo "== ${deployment}: hard corpus"
  o2pg ai-convert tests/fixtures/ai/lab.sql -o "${out}/corpus" \
    --deployment "${deployment}" --dsn "${DSN}" \
    --cases tests/fixtures/ai/cases.json --record "${out}/corpus-answers" || true
  echo "== ${deployment}: bundled sample (compile gate only)"
  o2pg ai-convert examples/sample_inventory.json -o "${out}/sample" \
    --deployment "${deployment}" --dsn "${DSN}" --record "${out}/sample-answers" || true
done

echo
for deployment in "$@"; do
  echo "${deployment}: eval/${deployment}/corpus/ai-report.md"
done
