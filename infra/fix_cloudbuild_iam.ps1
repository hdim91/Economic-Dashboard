# gcloud가 PATH에 있어야 함. 또는 절대 경로로 교체:
# $gcloud = 'C:\path\to\google-cloud-sdk\bin\gcloud.cmd'
$gcloud = 'gcloud'
$proj   = 'YOUR_GCP_PROJECT_ID'

$projNum = (& $gcloud projects describe $proj --format='value(projectNumber)' 2>&1 | Where-Object { $_ -match '^\d+$' })
Write-Host "Project Number: $projNum"

$cbSa = "serviceAccount:${projNum}@cloudbuild.gserviceaccount.com"
Write-Host "Cloud Build SA: $cbSa"

Write-Host "=== roles/run.admin 부여 ==="
& $gcloud projects add-iam-policy-binding $proj --member=$cbSa --role=roles/run.admin 2>&1 | Select-String "run.admin|Updated|ERROR"

Write-Host "=== roles/iam.serviceAccountUser 부여 ==="
& $gcloud projects add-iam-policy-binding $proj --member=$cbSa --role=roles/iam.serviceAccountUser 2>&1 | Select-String "serviceAccountUser|Updated|ERROR"

Write-Host "=== roles/artifactregistry.writer 부여 ==="
& $gcloud projects add-iam-policy-binding $proj --member=$cbSa --role=roles/artifactregistry.writer 2>&1 | Select-String "artifactregistry|Updated|ERROR"

Write-Host "완료"
