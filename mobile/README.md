# Dear Diane Phone

Flutter Android shell for the Dear Diane workspace phone UI.

Build the debug APK:

```sh
flutter build apk --debug --dart-define=DAN_API_BASE=http://10.77.77.2:8000
```

Optional Dear Diane phone WireGuard config can be baked in without using codexx keys:

```sh
flutter build apk --debug \
  --dart-define=DAN_API_BASE=http://10.77.77.2:8000 \
  --dart-define=DAN_PHONE_WIREGUARD_CONFIG_B64=<base64-config>
```

By default the app does not silently start its Android VPN tunnel, so it will not
replace another app's active VPN. Add
`--dart-define=DAN_PHONE_WIREGUARD_AUTOSTART=true` only for a DAN-specific build
where automatic tunnel startup is intended.

The native Android VPN bridge is intentionally DAN-scoped: package
`com.dan.dan_phone`, method channel `dan/wireguard`, tunnel `dan-phone`.
The backend `/api/workspace-wireguard` endpoint remains read-only for host
WireGuard services.
