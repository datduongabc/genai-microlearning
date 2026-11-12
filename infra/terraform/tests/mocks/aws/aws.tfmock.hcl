# Values a real AWS would return, shared by every test file. Unlisted attributes get random strings,
# which fail where Terraform expects an ARN.
mock_data "aws_availability_zones" {
  defaults = { names = ["ap-southeast-1a", "ap-southeast-1b", "ap-southeast-1c"] }
}
mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}
mock_data "aws_ec2_managed_prefix_list" {
  defaults = {
    id      = "pl-3b927c52"
    entries = [{ cidr = "13.32.0.0/15", description = "" }, { cidr = "52.84.0.0/15", description = "" }]
  }
}
mock_data "aws_iam_policy_document" {
  defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}
mock_resource "aws_ecs_task_definition" {
  defaults = { arn = "arn:aws:ecs:ap-southeast-1:123456789012:task-definition/x:1" }
}
mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/x" }
}
mock_resource "aws_s3_bucket" {
  defaults = { arn = "arn:aws:s3:::x", bucket = "x" }
}
mock_resource "aws_ssm_parameter" {
  defaults = { arn = "arn:aws:ssm:ap-southeast-1:123456789012:parameter/x" }
}
mock_resource "aws_cloudfront_distribution" {
  defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/x" }
}
mock_resource "aws_service_discovery_http_namespace" {
  defaults = { arn = "arn:aws:servicediscovery:ap-southeast-1:123456789012:namespace/ns-x" }
}
mock_resource "aws_lb_target_group" {
  defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:targetgroup/x/1" }
}
mock_resource "aws_appautoscaling_policy" {
  defaults = { arn = "arn:aws:autoscaling:ap-southeast-1:123456789012:scalingPolicy:x" }
}
mock_resource "aws_cloudfront_function" {
  defaults = { arn = "arn:aws:cloudfront::123456789012:function/x" }
}
mock_resource "aws_sns_topic" {
  defaults = { arn = "arn:aws:sns:ap-southeast-1:123456789012:x" }
}
