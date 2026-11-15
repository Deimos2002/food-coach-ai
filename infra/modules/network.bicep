// VNet with the Container Apps and Postgres subnets (spec §2).

param env string
param location string

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: 'vnet-foodcoach-${env}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.40.0.0/24'
      ]
    }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.40.0.0/26'
          delegations: [
            {
              name: 'aca'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-pg'
        properties: {
          addressPrefix: '10.40.0.64/28'
          delegations: [
            {
              name: 'pg'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output acaSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-aca')
output pgSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-pg')
