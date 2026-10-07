// Minimal Azure Database for PostgreSQL Flexible Server for trying o2pg output.
//
// Burstable B1ms, 32 GB storage, no high availability, 7-day backups, public
// access limited to one client IP. This size is covered by the Azure free
// account offer for PostgreSQL Flexible Server (check current terms); outside
// that offer it is billed per hour. See infra/README.md.

targetScope = 'resourceGroup'

@description('Server name. Must be globally unique, lower case, 3-63 characters.')
@minLength(3)
@maxLength(63)
param serverName string = 'o2pg-${uniqueString(resourceGroup().id)}'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Administrator login name.')
param administratorLogin string = 'o2pgadmin'

@description('Administrator password.')
@secure()
param administratorLoginPassword string

@description('The single public IPv4 address allowed through the firewall, for example your laptop.')
param clientIpAddress string

@description('PostgreSQL major version. 15 or later gives you MERGE.')
@allowed([
  '15'
  '16'
  '17'
])
param postgresVersion string = '16'

@description('Extensions to allow-list. The ones o2pg recommends most often.')
param allowedExtensions string = 'ORAFCE,PG_CRON,PG_PARTMAN,POSTGIS,PGCRYPTO,PG_STAT_STATEMENTS,POSTGRES_FDW'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: postgresVersion
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

resource clientFirewallRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: server
  name: 'allow-client-ip'
  properties: {
    startIpAddress: clientIpAddress
    endIpAddress: clientIpAddress
  }
}

resource extensionAllowList 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: server
  name: 'azure.extensions'
  properties: {
    value: allowedExtensions
    source: 'user-override'
  }
  dependsOn: [
    clientFirewallRule
  ]
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output psqlCommand string = 'psql "host=${server.properties.fullyQualifiedDomainName} port=5432 dbname=postgres user=${administratorLogin} sslmode=require"'
