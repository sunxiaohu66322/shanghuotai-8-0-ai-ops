$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$url = 'http://127.0.0.1:9191/'
$args = @('--headless=old','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--virtual-time-budget=20000','--dump-dom',$url)
$dom = & $chrome @args 2>$devnull
if ($dom) {
    $has7 = $dom | Select-String -Pattern 'dd-admin' -SimpleMatch
    if ($has7) { Write-Host 'DOM has dd-admin: True' } else { Write-Host 'DOM has dd-admin: False' }
    $hasShop = $dom | Select-String -Pattern 'LZL|乐群云|专卖店' -SimpleMatch
    if ($hasShop) { Write-Host 'DOM has shop: True' } else { Write-Host 'DOM has shop: False' }
    $len = $dom.Length
    Write-Host 'DOM length: ' + $len
} else {
    Write-Host 'NO DOM'
}
