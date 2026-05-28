# gcloud가 PATH에 있어야 함. 또는 절대 경로로 교체:
# $gcloud = 'C:\path\to\google-cloud-sdk\bin\gcloud.cmd'
$gcloud = 'gcloud'
$sa     = "serviceAccount:YOUR_SERVICE_ACCOUNT@$proj.iam.gserviceaccount.com"
$proj   = 'YOUR_GCP_PROJECT_ID'

Write-Host "=== ECOS_API_KEY IAM 부여 ==="
& $gcloud secrets add-iam-policy-binding ECOS_API_KEY `
    --project $proj --member $sa --role roles/secretmanager.secretAccessor

Write-Host "=== FRED_API_KEY IAM 부여 ==="
& $gcloud secrets add-iam-policy-binding FRED_API_KEY `
    --project $proj --member $sa --role roles/secretmanager.secretAccessor

Write-Host "완료"
