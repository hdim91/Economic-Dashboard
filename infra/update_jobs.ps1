# gcloud가 PATH에 있어야 함. 또는 절대 경로로 교체:
# $gcloud = 'C:\path\to\google-cloud-sdk\bin\gcloud.cmd'
$gcloud = 'gcloud'
$proj   = 'YOUR_GCP_PROJECT_ID'
$region = 'asia-northeast3'

Write-Host "=== pipeline-job 환경변수 + 시크릿 추가 ==="
& $gcloud run jobs update pipeline-job `
    --region $region --project $proj `
    --update-env-vars FINANCE_BQ_DATASET=finance_stats `
    --update-secrets ECOS_API_KEY=ECOS_API_KEY:latest,FRED_API_KEY=FRED_API_KEY:latest

Write-Host ""
Write-Host "=== report-job 환경변수 추가 ==="
& $gcloud run jobs update report-job `
    --region $region --project $proj `
    --update-env-vars FINANCE_BQ_DATASET=finance_stats

Write-Host ""
Write-Host "=== 완료 ==="
