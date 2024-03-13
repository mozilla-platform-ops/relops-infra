## define the root URI
$InfobloxServer = "https://infoblox1.private.mdc1.mozilla.com"
## use your username and password from sso
$InfobloxCredential = Get-Credential

## import the csv with all hosts
$nuc13 = Import-Csv -Path "~/Downloads/NUC2024BUILD.csv"

foreach ($nuc in $nuc13) {
    Start-Sleep -Seconds 3
    $asset_tag = $nuc.asset_tag
    $serial = $nuc.serial
    $rack_order = $nuc.rack_order
    $system_rack_id = $nuc.'system_rack % id'
    $switch_port = $nuc.switch_ports
    $oob_switch_port = $nuc.'OOB_switch_&_port'
    $pdu1 = $nuc.pdu1
    
    $uri = "$infobloxserver/wapi/v2.11.5/record:host"
    $data = @{
        ipv4addrs         = @(
            @{        
                ipv4addr = $nuc.ip
                mac      = $nuc.mac
            }
        )
        name              = $nuc.hostname
        comment           = @"
asset_tag = $asset_tag
serial = $serial
rack_order = $rack_order
system_rack_id = $system_rack_id
switch_port = $switch_port
oob_switch_port = $oob_switch_port
pdu1 = $pdu1
"@
        configure_for_dns = $true
        view              = "Private"
        ddns_protected    = $true
    }
    
    $created = Invoke-RestMethod -Uri $uri -Method Post -Body ($data | ConvertTo-Json) -Credential $InfobloxCredential -ContentType "application/json"
    if ($created) {
        Write-host "Created $($nuc.hostname) | $(Get-Date)"
    }
}
