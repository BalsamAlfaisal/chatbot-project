output "vm_public_ip" {
  value = azurerm_public_ip.chatbot.ip_address
}