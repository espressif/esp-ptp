idf_component_register(SRCS "ptpd.c"
                       PRIV_REQUIRES esp_eth esp_netif
                       INCLUDE_DIRS "include"
                       PRIV_INCLUDE_DIRS ".")

target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-unused-function)
