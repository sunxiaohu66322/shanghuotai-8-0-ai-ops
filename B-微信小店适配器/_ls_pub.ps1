$ErrorActionPreference = 'SilentlyContinue'
$d = 'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\crawl3'
Get-ChildItem $d -Filter 'pub-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 15 |
  ForEach-Object { '{0}  {1,8}  {2}' -f $_.LastWriteTime.ToString('MM-dd HH:mm'), $_.Length, $_.Name }
