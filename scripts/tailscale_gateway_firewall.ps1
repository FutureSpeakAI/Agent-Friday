# Friday TV gateway: allow 47810/TCP in on the Tailscale adapter ONLY. Run once from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\tailscale_gateway_firewall.ps1          (add)
#   powershell -ExecutionPolicy Bypass -File scripts\tailscale_gateway_firewall.ps1 -Remove  (undo)
# The rule is bound to the interface named Tailscale and to tailnet source addresses (100.64.0.0/10), so it
# opens nothing on Wi-Fi, Ethernet or any VPN. UDP 47811 (LAN discovery) is not opened and is never offered
# on the tailnet. The home-network rule for the LAN address is unchanged by this script.
param([switch]$Remove, [int]$Port = 47810)
$name = "Friday TV gateway (Tailscale only)"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this from an elevated (Administrator) PowerShell."; exit 1
}
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) { Write-Host "Removed: $name"; exit 0 }
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -InterfaceAlias "Tailscale" -RemoteAddress "100.64.0.0/10" -Profile Any `
    -Description "Paired Friday devices reaching this PC over Tailscale. Pairing stays LAN-only." | Out-Null
Get-NetFirewallRule -DisplayName $name | Get-NetFirewallPortFilter | Format-Table Protocol, LocalPort
Write-Host "Added: $name (TCP $Port, Tailscale adapter only)"
