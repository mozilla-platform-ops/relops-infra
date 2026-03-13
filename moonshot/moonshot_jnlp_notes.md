# Moonshot iLO Java IRC JNLP Generation

Notes on how the HP Moonshot iLO generates JNLP files for the Java Integrated Remote Console (IRC), reverse-engineered from the iLO web UI (`moonshot-built.js`).

## Auth Flow

1. `POST /rest/v1/Sessions` with `{"UserName": "...", "Password": "..."}` → response includes `X-Auth-Token` header
2. `POST /rest/v1/Chassis/1/Cartridges/C{N}` with body `{"Action": "RemoteConsoleSession", "Type": "Create", "UserName": "..."}` and `X-Auth-Token` header → response contains session token in `Messages[0].MessageArgs[0]`

## JNLP Parameter Sources

| Parameter | Value | Source |
|-----------|-------|--------|
| `RCINFO1` | console session token | `Messages[0].MessageArgs[0][:32]` from RemoteConsoleSession POST |
| `RCINFOLANG` | `en` | iLO client language |
| `INFO0` | `7AC3BDEBC9AC64E85734454B53BB73CE` | **hardcoded** in iLO firmware JS — identical across all chassis and sessions |
| `INFO1` | `17988` | **hardcoded** in iLO firmware JS — fixed KVM port |
| `INFO2` | `composite` | **hardcoded** |

## Codebase URL Port

The `codebase` and `jar` URLs use a port derived from the cartridge slot number:

```
port = cartridge_slot + 735
```

Examples:
- Cartridge 1 → port 736
- Cartridge 31 → port 766
- Cartridge 45 → port 780

This is **not** a fixed port — it varies per cartridge. Using the wrong port causes the Java applet to connect to a different cartridge than intended.

## JNLP Structure

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<jnlp spec="1.0+" codebase="https://{chassis}:{slot+735}" href="">
  ...
  <jar href="https://{chassis}:{slot+735}/html/intgapp4_231.jar" main="false" />
  ...
  <applet-desc main-class="com.hp.ilo2.intgapp.intgapp" name="iLOJIRC"
      documentbase="https://{chassis}:{slot+735}/html/java_irc.html" width="1" height="1">
    <param name="RCINFO1" value="{MessageArgs[0][:32]}" />
    <param name="RCINFOLANG" value="en" />
    <param name="INFO0" value="7AC3BDEBC9AC64E85734454B53BB73CE" />
    <param name="INFO1" value="17988" />
    <param name="INFO2" value="composite" />
  </applet-desc>
</jnlp>
```

## JAR File

`intgapp4_231.jar` — the version suffix (`231`) may change with firmware updates. The iLO JS falls back to this default if `CartridgeModel.getJarFileName()` returns null.

## Chassis Hostname

`moon-chassis-{N}.inband.releng.mdc{1|2}.mozilla.com` — chassis 1–7 are in mdc1, 8+ in mdc2.

## Worker Slot → Cartridge Mapping

See `hostname_to_cart()` in `moonshot_lib.py`. Returns `(chassis_fqdn, cartridge_number)` from a worker hostname like `t-linux64-ms-001`.
