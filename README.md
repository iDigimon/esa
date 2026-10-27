# Certbot DNS 阿里云 ESA 插件

通过阿里云边缘安全加速（ESA）API 自动创建 TXT 记录，完成 Certbot DNS-01 验证，申请及续期普通域名和泛域名证书。

本插件适用于 **NS 接入 ESA** 的域名，且 NS 委派须已生效；CNAME 接入仍需使用原 DNS 服务商的 Certbot 插件。参见 [ESA DNS 接入方式](https://help.aliyun.com/zh/edge-security-acceleration/esa/user-guide/dns-overview/)。

## 功能特性

- 自动分页查询站点，按 DNS 标签边界选择最长匹配项，无需手动填写站点 ID
- 支持普通域名、子域名、泛域名及国际化域名
- 支持多个挑战共用记录名，仅清理本次创建的记录，保留已有 TXT 记录
- 支持 AccessKey 和 STS 临时凭证
- 支持 Certbot 3、4、5 和 Python 3.9–3.15

Python 3.9 支持 Certbot 3/4，Python 3.10–3.13 支持 Certbot 3/4/5，Python 3.14–3.15 支持 Certbot 4/5。使用 Certbot 3 时需额外安装 `pyopenssl<25`。

## 安装

在实际运行 Certbot 的 Python 环境中，从源码安装：

```bash
pip install .
certbot plugins --text
```

输出应包含 `dns-esa`。使用 Certbot 3 时执行 `pip install 'pyopenssl<25'`。

## 配置文件

保存为 `~/esa.ini`：

```ini
dns_esa_access_key_id = YOUR_ACCESS_KEY_ID
dns_esa_access_key_secret = YOUR_ACCESS_KEY_SECRET
# 可选，默认 cn-hangzhou；国际站可使用 ap-southeast-1
dns_esa_region_id = cn-hangzhou
# 可选，仅使用 STS 临时凭证时填写
# dns_esa_security_token = YOUR_SECURITY_TOKEN
```

`region_id` 应与站点所在的 API 地域一致。插件只查询所选地域，不会跨地域猜测。支持的公网接入点为杭州和新加坡，参见 [ESA 服务接入点](https://help.aliyun.com/zh/edge-security-acceleration/esa/api-esa-2024-09-10-endpoint)。使用 STS 时，续期前需确保凭证仍有效。

限制凭证文件权限：

```bash
chmod 600 ~/esa.ini
```

Windows 用户请通过系统文件权限限制访问。

## 运行

```bash
certbot certonly \
  --authenticator dns-esa \
  --dns-esa-credentials ~/esa.ini \
  --dns-esa-propagation-seconds 30 \
  -d '*.example.com' \
  -d example.com
```

DNS 传播等待默认 30 秒，可根据实际情况增加。TXT TTL 默认 600 秒，可通过 `--dns-esa-ttl` 调整；`1` 表示 ESA 自动 TTL，其余允许值取决于服务限制。创建记录超时后插件不会自动重试写入，以避免重复创建。

后续使用 `certbot renew` 自动续期。可先使用 `certbot renew --dry-run` 检查续期配置。

## 阿里云权限配置

AccessKey 需具备 `AliyunESAFullAccess`，或包含以下操作的自定义 RAM 权限：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "esa:ListSites",
        "esa:ListRecords",
        "esa:CreateRecord",
        "esa:DeleteRecord"
      ],
      "Resource": "*"
    }
  ]
}
```

`ListSites` 和 `DeleteRecord` 需要 `Resource: "*"`；`ListRecords` 和 `CreateRecord` 可进一步限制到指定站点。参见 [ListSites](https://help.aliyun.com/zh/edge-security-acceleration/esa/api-esa-2024-09-10-listsites)、[ListRecords](https://help.aliyun.com/zh/edge-security-acceleration/esa/api-esa-2024-09-10-listrecords)、[CreateRecord](https://help.aliyun.com/zh/edge-security-acceleration/esa/api-esa-2024-09-10-createrecord)、[DeleteRecord](https://help.aliyun.com/zh/edge-security-acceleration/esa/api-esa-2024-09-10-deleterecord)。

## 自动部署

本项目只负责 Certbot DNS 验证；申请后的自动部署可配合 [AutoCert](https://github.com/tiyee/AutoCert)。
