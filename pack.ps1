# 小R的zhenxun插件打包脚本
# 用法: .\pack.ps1
# 会在当前目录生成 4 个插件的 zip 包

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $root "dist"
Remove-Item -Recurse -Force $outDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$plugins = @(
    "zhenxun_bot_route2",
    "nonebot_plugin_handle",
    "nonebot_plugin_handle2",
    "nonebot_plugin_steam_info"
)

foreach ($name in $plugins) {
    $src = Join-Path $root $name
    $zip = Join-Path $outDir "$name.zip"
    Write-Host "打包 $name ..."
    Compress-Archive -Path "$src\*" -DestinationPath $zip -Force
    $size = [math]::Round((Get-Item $zip).Length / 1KB, 1)
    Write-Host "  -> $name.zip ($size KB)"
}

Write-Host ""
Write-Host "完成！4 个 zip 已生成到 dist\ 目录"
Write-Host "请上传到 GitHub Release: https://github.com/RShock/xiaor_zhenxun_plugin/releases/new"
