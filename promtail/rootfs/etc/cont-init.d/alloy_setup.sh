#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant Add-on: Alloy
# This file makes the config file from inputs
# ==============================================================================
readonly CONFIG_DIR=/etc/alloy
readonly CONFIG_FILE="${CONFIG_DIR}/config.alloy"
readonly BASE_CONFIG="${CONFIG_DIR}/base_config.alloy"
readonly DEF_SCRAPE_CONFIGS="${CONFIG_DIR}/default-scrape-config.alloy"
readonly CUSTOM_SCRAPE_CONFIGS="${CONFIG_DIR}/custom-scrape-config.alloy"
declare cafile
declare add_stages
declare add_scrape_configs

bashio::log.info 'Setting base config for Alloy...'
cp "${BASE_CONFIG}" "${CONFIG_FILE}"

# Build enhanced loki.write configuration with auth/TLS if needed
loki_write_config="loki.write \"default\" {
  endpoint {
    url = env(\"URL\")"

# Add basic auth configuration if provided
if ! bashio::config.is_empty 'client.username'; then
    bashio::log.info 'Adding basic auth to client config...'
    bashio::config.require 'client.password' "'client.username' is specified"
    
    loki_write_config="${loki_write_config}
    
    basic_auth {
      username = \"$(bashio::config 'client.username')\"
      password = \"$(bashio::config 'client.password')\"
    }"
fi

# Add TLS configuration if provided
if ! bashio::config.is_empty 'client.cafile'; then
    bashio::log.info "Adding TLS to client config..."
    cafile="/ssl/$(bashio::config 'client.cafile')"

    if ! bashio::fs.file_exists "${cafile}"; then
        bashio::log.fatal
        bashio::log.fatal "The file specified for 'cafile' does not exist!"
        bashio::log.fatal "Ensure the CA certificate file exists and full path is provided"
        bashio::log.fatal
        bashio::exit.nok
    fi
    
    loki_write_config="${loki_write_config}
    
    tls_config {
      ca_file = \"${cafile}\""
    
    if ! bashio::config.is_empty 'client.servername'; then
        loki_write_config="${loki_write_config}
      server_name = \"$(bashio::config 'client.servername')\""
    fi
    
    if ! bashio::config.is_empty 'client.certfile'; then
        bashio::log.info "Adding mTLS to client config..."
        bashio::config.require 'client.keyfile' "'client.certfile' is specified"
        
        if ! bashio::fs.file_exists "$(bashio::config 'client.certfile')"; then
            bashio::log.fatal
            bashio::log.fatal "The file specified for 'certfile' does not exist!"
            bashio::log.fatal "Ensure the certificate file exists and full path is provided"
            bashio::log.fatal
            bashio::exit.nok
        fi
        
        if ! bashio::fs.file_exists "$(bashio::config 'client.keyfile')"; then
            bashio::log.fatal
            bashio::log.fatal "The file specified for 'keyfile' does not exist!"
            bashio::log.fatal "Ensure the key file exists and full path is provided"
            bashio::log.fatal
            bashio::exit.nok
        fi
        
        loki_write_config="${loki_write_config}
      cert_file = \"$(bashio::config 'client.certfile')\"
      key_file = \"$(bashio::config 'client.keyfile')\""
    fi
    
    loki_write_config="${loki_write_config}
    }"
fi

# Close the loki.write block
loki_write_config="${loki_write_config}
  }
}

"

# Replace the default loki.write block with the enhanced one if auth/TLS is configured
if ! bashio::config.is_empty 'client.username' || ! bashio::config.is_empty 'client.cafile'; then
    # Remove the default loki.write block and replace with enhanced version
    sed -i '/^loki.write "default"/,/^}/d' "${CONFIG_FILE}"
    echo "${loki_write_config}" >> "${CONFIG_FILE}"
fi

# Add scrape configurations
if bashio::config.true 'skip_default_scrape_config'; then
    bashio::log.info 'Skipping default journald scrape config...'
    if ! bashio::config.is_empty 'additional_pipeline_stages'; then
        bashio::log.warning
        bashio::log.warning "'additional_pipeline_stages' ignored since 'skip_default_scrape_config' is true!"
        bashio::log.warning 'See documentation for more information.'
        bashio::log.warning
    fi
    bashio::config.require 'additional_scrape_configs' "'skip_default_scrape_config' is true"
else
    bashio::log.info "Adding default journald scrape config..."
    cat "${DEF_SCRAPE_CONFIGS}" >> "${CONFIG_FILE}"
    
    if ! bashio::config.is_empty 'additional_pipeline_stages'; then
        bashio::log.info "Adding additional pipeline stages to default journal scrape config..."
        add_stages="$(bashio::config 'additional_pipeline_stages')"
        
        if ! bashio::fs.file_exists "${add_stages}"; then
            bashio::log.fatal
            bashio::log.fatal "The file specified for 'additional_pipeline_stages' does not exist!"
            bashio::log.fatal "Ensure the file exists at the path specified"
            bashio::log.fatal
            bashio::exit.nok
        fi
        
        # Append additional pipeline stages to the config
        bashio::log.info "Appending additional pipeline stages..."
        cat "${add_stages}" >> "${CONFIG_FILE}"
    fi
fi

# Add custom scrape configurations
if ! bashio::config.is_empty 'additional_scrape_configs'; then
    bashio::log.info "Adding custom scrape configs..."
    add_scrape_configs="$(bashio::config 'additional_scrape_configs')"
    
    if ! bashio::fs.file_exists "${add_scrape_configs}"; then
        bashio::log.fatal
        bashio::log.fatal "The file specified for 'additional_scrape_configs' does not exist!"
        bashio::log.fatal "Ensure the file exists at the path specified"
        bashio::log.fatal
        bashio::exit.nok
    fi
    
    cat "${add_scrape_configs}" >> "${CONFIG_FILE}"
fi

bashio::log.info "Alloy configuration generated successfully"