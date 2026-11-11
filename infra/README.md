# Triển khai lên AWS

Terraform cho bản đồ án của `docs/architecture/system-design-v1.md`: một thư mục, một môi trường mỗi lần `apply`, xóa được bằng một lệnh.

**Mới `apply` được một phần trên AWS.** Lần `apply` ngày 2026-11-06 tạo được mạng, RDS, Valkey, ALB, S3, SNS và budget, rồi dừng ở CloudFront vì tài khoản chưa được AWS xác minh (đã xác minh xong ngày 2026-11-10); stack đó đã được xóa. Các ECS service, migration trên RDS, Service Connect, upload thẳng vào S3, autoscaling và cảnh báo chưa từng chạy trên AWS thật: chúng mới chỉ qua `terraform validate` và `terraform test` (plan với AWS giả lập, xem `terraform/tests/`), nên lần `apply` tới vẫn có thể gặp lỗi mà chỉ AWS thật mới báo.

## Có gì

| Phần | Tài nguyên | Ghi chú |
| --- | --- | --- |
| Mạng | VPC 2 AZ, subnet public cho task, subnet data không có đường ra Internet, S3 gateway endpoint | Không NAT: task có IP công khai, không có rule inbound từ Internet |
| Vào | CloudFront (`/api/*` tới ALB, còn lại là frontend trong S3), ALB chỉ nhận từ CloudFront | ALB kiểm header bí mật `X-Origin-Verify`, health check `/ready` |
| Tính toán | ECS Fargate: `api`, `worker`, `llm-gateway`, `embedder`, và `reranker` khi `reranker_provider = "jina"` | Service Connect: tên `gateway`, `embedder`, `reranker` giống compose |
| Dữ liệu | RDS PostgreSQL 16 (pgvector, bắt buộc TLS), ElastiCache Valkey một node, S3 cho tệp tải lên | Trình duyệt gửi tệp thẳng vào S3 bằng presigned POST (CORS chỉ cho phép POST từ địa chỉ CloudFront); tệp tự hết hạn sau 7 ngày, phòng job lỗi bỏ lại |
| Bí mật | Parameter Store SecureString | Khóa nhà cung cấp chỉ tới `llm-gateway`; URL chủ DB chỉ tới task migrate |
| Scale | `api` theo request/task và CPU; `worker` theo `JobsReady`; `embedder` theo CPU | Worker có job đang chạy được bảo vệ khỏi scale-in |
| Giám sát | Log JSON 14 ngày, cảnh báo qua SNS, AWS Budgets $50/$100/$150 | Cảnh báo Jev chỉ có khi `reranker_provider = "jev"` |

## Lần đầu

Cần: AWS CLI đã đăng nhập, Docker, Node, `jq`, Terraform ≥ 1.10.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # sửa image_tag = commit hiện tại, alarm_email
terraform init

# 1. Kho ECR trước, để có chỗ đẩy image.
terraform apply -target=aws_ecr_repository.repo -target=aws_ecr_lifecycle_policy.keep_recent
IMAGES_ONLY=1 ../scripts/deploy.sh             # thêm WITH_RERANKER=1 nếu dùng jina

# 2. Phần còn lại. Service khởi động trước migration, nên api chưa qua /ready và lần deploy đầu có
#    thể bị circuit breaker đánh dấu thất bại; bước 4 khởi động lại service đó sau migration.
#    Topic SNS mới gửi email xác nhận tới alarm_email: bấm Confirm, mỗi lần tạo lại stack.
terraform apply

# 3. Khóa nhà cung cấp. Terraform không bao giờ thấy chúng và không ghi đè sau lần tạo.
PREFIX=$(terraform output -raw ssm_prefix)
aws ssm put-parameter --overwrite --type SecureString --name "$PREFIX/openrouter-api-key" --value "..."
#   tương tự: typesafe-api-key, moodle-token
aws ecs update-service --cluster "$(terraform output -raw cluster)" --service gateway --force-new-deployment

# 4. Migration, frontend, smoke test.
SKIP_BUILD=1 ../scripts/deploy.sh
```

## Mỗi lần deploy

```bash
infra/scripts/deploy.sh
```

Thứ tự: build và đẩy image theo commit → chạy migration bằng image mới trong một task riêng → rolling update → frontend → gọi `/api/ready` qua CloudFront. Migration lỗi thì dừng, chưa service nào đổi. Task mới không qua health check thì circuit breaker của ECS tự quay về bản cũ.

Trong lúc rolling update, bản cũ và mới chạy cùng lúc trên schema mới, nên thay đổi phá vỡ tương thích phải tách hai lần deploy: thêm trước, bỏ sau.

## Moodle cho bản AWS

Moodle không nằm trong stack. Để thử LTI với một Moodle thật mà không tốn thêm tiền, chạy Moodle của kho mã trên máy mình và đưa nó ra Internet qua Cloudflare quick tunnel (không cần tài khoản Cloudflare):

```bash
bash infra/scripts/moodle-tunnel.sh    # sau deploy.sh; Ctrl-C để dừng
```

Script dựng Moodle 5.2 dùng một lần (Postgres và moodledata trên tmpfs, không đụng stack compose), tạo khóa học, một giảng viên, một sinh viên và công cụ LTI trỏ tới output `url`, đăng ký Moodle này vào database trên AWS bằng một task ECS, rồi in địa chỉ và mật khẩu. Mở khóa học bằng tài khoản giảng viên trước, sau đó tới sinh viên.

Giới hạn:

- **Địa chỉ đổi mỗi lần chạy,** và issuer LTI đổi theo: tài khoản và khóa học tạo qua lần chạy trước không được nhận lại. Giữ script chạy suốt buổi demo; máy ngủ hoặc mất mạng là mất Moodle đó.
- **Chỉ có LTI** (launch và danh sách lớp). Xuất Book và Quiz sang Moodle cần thêm `moodle_base_url`, `moodle_wwwroot` bằng địa chỉ tunnel và `moodle-token` trong SSM, tức một lần `terraform apply` nữa cho mỗi địa chỉ.
- Lưu lượng đi qua Cloudflare, nên chỉ dùng với dữ liệu thử.

Cần một địa chỉ ổn định thì thay bằng một EC2 nhỏ chạy Moodle (khoảng $0,02 đến $0,03 một giờ), chưa có trong Terraform này.

## Xóa

```bash
terraform destroy
```

Còn lại sau khi xóa: snapshot cuối của RDS (`microlearning-<environment>-final-<8 ký tự hex>`, tên mới mỗi lần nên không đụng snapshot của lần trước). Muốn xóa hẳn: `aws rds delete-db-snapshot --db-snapshot-identifier <tên>`.

Kho ECR cũng bị xóa cùng image, nên lần dựng sau bắt đầu lại từ bước 1 và đẩy lại khoảng 2.5 GB image.

## Quyết định và giới hạn

- **Worker không về 0.** Chính worker báo độ sâu hàng đợi. Không còn worker nào thì không có metric, và không gì scale nó lên lại. Biến `worker.min` bị chặn ở mức tối thiểu 1.
- **Scale-in không giết job.** Worker có job đang chạy xin ECS bảo vệ (`app/jobs/ecs_protection.py`). Khi task bị dừng vì deploy, worker cho job 100 s để xong, rồi trả job về hàng đợi mà không tính là một lần thử. Trước đây job đó phải chờ sweeper 15 phút.
- **Địa chỉ client.** Uvicorn chỉ tin VPC (ALB) và dải IP của CloudFront, nên rate limit theo IP thấy đúng địa chỉ người dùng mà người dùng không giả được. Tin `*` thì client tự ghi được `X-Forwarded-For`.
- **CloudFront tới ALB là HTTP.** HTTPS ở chặng này cần tên miền và chứng chỉ riêng. Chặng người dùng tới CloudFront vẫn là HTTPS.
- **Ngân sách kết nối DB.** `check "connection_budget"` báo khi `api.max` và `worker.max` cộng lại có thể mở nhiều kết nối hơn RDS nhận. Mặc định: 349 trên 407.
- **Model embedder nằm trong image** (`devops/embedder`), như reranker. Task mới không phải tải 2,4 GB từ Hugging Face trước khi phục vụ, và không phụ thuộc Hugging Face lúc chạy. Đổi model thì phải nhúng lại toàn bộ chunk, xem `devops/embedder/prepare_model.py`.
- **Moodle không nằm trong stack này.** Điền `moodle_base_url` nếu có.
- **Valkey không có mật khẩu,** chỉ có TLS và security group. Nó chỉ giữ bộ đếm rate limit.
