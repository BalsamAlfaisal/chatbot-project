terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "chatbot" {
  name     = var.resource_group_name
  location = "eastus"
}

resource "azurerm_virtual_network" "chatbot" {
  name                = "chatbot-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.chatbot.location
  resource_group_name = azurerm_resource_group.chatbot.name
}

resource "azurerm_subnet" "chatbot" {
  name                 = "chatbot-subnet"
  resource_group_name  = azurerm_resource_group.chatbot.name
  virtual_network_name = azurerm_virtual_network.chatbot.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "chatbot" {
  name                = "chatbot-nsg"
  location            = azurerm_resource_group.chatbot.location
  resource_group_name = azurerm_resource_group.chatbot.name

  security_rule {
    name                       = "allow-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix     = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow-chatbot"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                  = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8501"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_public_ip" "chatbot" {
  name                = "chatbot-public-ip"
  location            = azurerm_resource_group.chatbot.location
  resource_group_name = azurerm_resource_group.chatbot.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "chatbot" {
  name                = "chatbot-nic"
  location            = azurerm_resource_group.chatbot.location
  resource_group_name = azurerm_resource_group.chatbot.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.chatbot.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.chatbot.id
  }
}

resource "azurerm_network_interface_security_group_association" "chatbot" {
  network_interface_id      = azurerm_network_interface.chatbot.id
  network_security_group_id = azurerm_network_security_group.chatbot.id
}

resource "azurerm_linux_virtual_machine" "chatbot" {
  name                = "chatbot-vm"
  resource_group_name = azurerm_resource_group.chatbot.name
  location            = azurerm_resource_group.chatbot.location
  size                = "Standard_D2as_v7"
  admin_username      = "azureuser"

  network_interface_ids = [
    azurerm_network_interface.chatbot.id
  ]

  admin_ssh_key {
    username   = "azureuser"
    public_key = file("${path.module}/ssh-keys/terraform-azure.pub")
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }
  #stage 7
    identity {
    type = "SystemAssigned"
  }
}

#stage 7
data "azurerm_client_config" "current" {}

resource "random_string" "kv_suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_key_vault" "kv" {
  name                       = "proj-kv-${random_string.kv_suffix.result}"
  location                   = azurerm_resource_group.chatbot.location   
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
}

# صلاحية لك لإضافة الأسرار (تحل مشكلة الـ Guest تلقائياً)
resource "azurerm_role_assignment" "me_kv" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# صلاحية للـ VM لقراءة الأسرار
resource "azurerm_role_assignment" "vm_kv" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_virtual_machine.chatbot.identity[0].principal_id 
}

output "key_vault_name" {
  value = azurerm_key_vault.kv.name
}