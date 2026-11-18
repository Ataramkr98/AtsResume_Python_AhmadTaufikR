# AI match summaries with Groq

The candidate detail page shows each application's score, component scores,
matched skills and requirements not listed. Recruiters and administrators can
select **Analyze fit** to recalculate the score and generate a short AI summary.
The summary is saved with the match result and exposed through the existing API.

Resume extraction uses the deterministic PDF/DOCX parser. Numeric match scores
always use the documented rules; the language model only explains those results.
The prompt sends the job title, total score, skill overlap and warnings, without the
candidate's name, email, phone or resume file.

Set these environment variables on the server (or in the ignored local `.env`):

```dotenv
AI_BASE_URL=https://api.groq.com/openai/v1
AI_API_KEY=<your Groq API key>
AI_MODEL_DEFAULT=openai/gpt-oss-20b
AI_MODEL_ANALYZE=openai/gpt-oss-20b
AI_NARRATIVE_SCORES=true
AI_TIMEOUT_SECONDS=12
AI_MAX_RETRIES=1
AI_CACHE_SECONDS=900
```

`GROQ_API_KEY` can substitute for `AI_API_KEY`. Model selection uses the feature
override, then `AI_MODEL_CHAT`, then `AI_MODEL_DEFAULT`. Confirm the chosen model
is available to your account using Groq's `/models` endpoint.

Successful requests are cached for 15 minutes in a bounded worker-local cache;
input, provider, credential or model changes invalidate the cache key. Transient
HTTP errors are retried once by default, with a brief backoff. Failures are cached
for 30 seconds, and the page keeps the numeric score with an availability message.
Neither provider response bodies nor credentials are included in error logs.

The refresh endpoint requires POST, CSRF protection, organization scope and
candidate-management permission, and records an audit event. Regression tests
use mocked providers and never consume local API credentials.

Provider references: [Groq compatibility](https://console.groq.com/docs/openai),
[models](https://console.groq.com/docs/models).
