# NuttX PTP Daemon for ESP-IDF

IEEE 1588-2008 (PTPv2) daemon for ESP-IDF, ported from the [NuttX PTP daemon](https://github.com/apache/nuttx-apps/tree/master/netutils/ptpd).

The protocol is transported over **Ethernet at Layer 2**, following Annex F of IEEE 1588-2008. Synchronization timestamps are provided by the internal Ethernet MAC and delivered to the daemon through the **L2 TAP** interface.

This component requires a SoC with IEEE 1588v2 hardware timestamping (`SOC_EMAC_IEEE1588V2_SUPPORTED`), currently **ESP32-P4** and **ESP32-S31**.

## Add the Component

```bash
idf.py add-dependency "espressif/esp_ptp"
```

Or in your component or project manifest (`idf_component.yml`):

```yaml
dependencies:
  espressif/esp_ptp: "*"
```

## Usage

Enable L2 TAP in `sdkconfig` (`CONFIG_ESP_NETIF_L2_TAP=y`), initialize Ethernet and `esp_netif`, then start the daemon:

```c
#include "ptpd.h"

struct ptpd_config_s ptp_config = {
    .interface = "ETH_0",   // esp_netif if_key
    .clock = "ptpclock",
    .client_only = false,   // true: slave only
    .hardware_ts = true,
    .delay_e2e = true,      // send delay request messages
    .bmca = true,           // Best Master Clock Algorithm
    .af = AF_INET,
};

int pid = ptpd_start(&ptp_config);
if (pid < 0) {
    ESP_LOGE(TAG, "Failed to start PTP daemon");
}
```

Query status with `ptpd_status()` and stop the daemon with `ptpd_stop()`.

Daemon behaviour (domain, priorities, intervals, thresholds, and so on) is configured under **Component config → PTP Daemon Configuration**.

See the [PTP example](../examples/ptp) for a complete master/slave setup that also generates a GPIO pulse for measuring synchronization accuracy.

## Upstream Information

| Field | Value |
| --- | --- |
| upstream | https://github.com/apache/nuttx-apps |
| imported app | netutils/ptpd |
| upstream commit | `54bef649ea14d293128545f24c9db2594197a327` |
| date imported | 4/2/2026 |
