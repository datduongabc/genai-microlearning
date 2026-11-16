# One address for the whole application, as in compose behind Caddy: /api/* goes to the load
# balancer with the prefix removed, everything else is the built frontend in S3.

resource "aws_s3_bucket" "frontend" {
  bucket_prefix = "${local.name}-frontend-"
  force_destroy = true # rebuilt from the repository on every deploy
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudFrontOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.main.arn } }
    }]
  })
}

# Caddy's handle_path: the backend's routes have no /api prefix.
resource "aws_cloudfront_function" "strip_api_prefix" {
  name    = "${local.name}-strip-api"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var request = event.request;
      request.uri = request.uri.substring(4) || "/";
      return request;
    }
  JS
}

# The frontend routes in the browser, so /courses/42 must get index.html. A distribution-wide
# error page would do it but would also turn the API's own 404s into HTML.
resource "aws_cloudfront_function" "spa" {
  name    = "${local.name}-spa"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var request = event.request;
      var last = request.uri.split("/").pop();
      if (last.indexOf(".") === -1) {
        request.uri = "/index.html";
      }
      return request;
    }
  JS
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# The page renders model output, so a prompt-injected document is the likely attacker: the CSP keeps
# the page from loading or sending anything to a host of its choosing. connect-src holds the uploads
# bucket for the browser's presigned POST. override = false, because the api sets its own, stricter
# headers (app/middleware.py) and the LTI error page must stay frameable by any LMS.
resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${local.name}-security"

  security_headers_config {
    content_security_policy {
      override = false
      content_security_policy = join("; ", [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:", # Vite inlines one KaTeX font
        "img-src 'self' data: blob:",                      # slide previews come down with the token and show as blobs
        # Videos play from a presigned GET on the bucket, or without S3 from a blob of the API file.
        "media-src 'self' blob: https://${aws_s3_bucket.uploads.bucket_regional_domain_name}",
        "connect-src 'self' https://${aws_s3_bucket.uploads.bucket_regional_domain_name}",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        # Moodle opens the tool in a new window, but an LMS set to embed it needs to frame the page.
        "frame-ancestors ${try(regex("^https?://[^/]+", var.moodle_wwwroot), "'none'")}",
      ])
    }
    strict_transport_security {
      override                   = false
      access_control_max_age_sec = 31536000
      include_subdomains         = true
    }
    content_type_options {
      override = false
    }
  }
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  comment             = local.name
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # includes Singapore and the rest of Asia
  http_version        = "http2and3"

  origin {
    origin_id                = "frontend"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id   = "api"
    domain_name = aws_lb.main.dns_name

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }

    custom_origin_config {
      # CloudFront to the load balancer is plain HTTP: TLS there needs a domain and a
      # certificate of our own. The leg runs on AWS's network, and the viewer leg is HTTPS.
      origin_protocol_policy = "http-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"]
      # Time between two packets. A chat stream pings every 15 s, well inside it.
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id           = "frontend"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    compress                   = true
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa.arn
    }
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    # Compressing a chat stream would hold its events back until a block fills.
    compress = false

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.strip_api_prefix.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
