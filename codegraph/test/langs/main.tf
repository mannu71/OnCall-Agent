variable "region" { default = "us-east-1" }
resource "aws_instance" "web" { ami = "x" }
module "vpc" { source = "./vpc" }
output "ip" { value = "1" }
