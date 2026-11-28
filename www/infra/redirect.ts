import * as aws from "@pulumi/aws";

// astral.us (and www.astral.us) is the site's old domain. Every request gets a
// 301 to the same path + query string on https://coy.bot. Prod only: the
// astral.us hosted zone lives in this account, and only one stage can own it.
//
// Two ways to serve it; VIA picks one.
//
// "alb" (current): an Application Load Balancer in us-east-2 whose listeners
//   answer with a redirect action. No Lambda, no CloudFront. Costs roughly
//   US$24/month (hourly ALB charge plus two public IPv4 addresses).
//
// "cloudfront": a CloudFront distribution with a 584-byte viewer-request
//   function. Near-free, but CloudFront alternate domain names are unique
//   across ALL AWS accounts, and the old (compromised, on-hold) account's
//   distributions still hold astral.us and www.astral.us. Deploying it fails
//   with CNAMEAlreadyExists until AWS Support releases those names. Switch to
//   it then, and the ALB is torn down by the same deploy. Built on
//   sst.aws.Cdn + our own function, not sst.aws.Router: Router prepends this
//   code to its own routing function and exceeds CloudFront Functions' 10 KB
//   limit (FunctionSizeLimitExceeded).
const VIA: "alb" | "cloudfront" = "alb";

const targetHost = "coy.bot";
const names = ["astral.us", "www.astral.us"];

function createAlbRedirect() {
  const vpc = aws.ec2.getVpcOutput({ default: true });
  const subnets = aws.ec2.getSubnetsOutput({
    filters: [
      { name: "vpc-id", values: [vpc.id] },
      { name: "default-for-az", values: ["true"] },
    ],
  });

  const sg = new aws.ec2.SecurityGroup("AstralRedirectSg", {
    description: "astral.us redirect ALB: HTTP/HTTPS in, nothing out",
    vpcId: vpc.id,
    ingress: [80, 443].map((port) => ({
      protocol: "tcp",
      fromPort: port,
      toPort: port,
      cidrBlocks: ["0.0.0.0/0"],
    })),
  });

  const lb = new aws.lb.LoadBalancer("AstralRedirectLb", {
    loadBalancerType: "application",
    internal: false,
    ipAddressType: "ipv4",
    securityGroups: [sg.id],
    // An ALB needs at least two AZs. Each subnet costs a billed public IPv4
    // address, so use exactly two.
    subnets: subnets.ids.apply((ids) => [...ids].sort().slice(0, 2)),
    dropInvalidHeaderFields: true,
  });

  const zone = aws.route53.getZoneOutput({ name: "astral.us", privateZone: false });

  // Regional (us-east-2) cert for the HTTPS listener.
  const cert = new aws.acm.Certificate("AstralRedirectAlbCert", {
    domainName: names[0],
    subjectAlternativeNames: names.slice(1),
    validationMethod: "DNS",
  });
  const validationRecords = names.map((name, i) => {
    const opt = cert.domainValidationOptions.apply(
      (opts) => opts.find((o) => o.domainName === name)!,
    );
    return new aws.route53.Record(`AstralRedirectAlbCertValidation${i}`, {
      zoneId: zone.zoneId,
      name: opt.resourceRecordName,
      type: opt.resourceRecordType,
      records: [opt.resourceRecordValue],
      ttl: 300,
      // ACM reuses the same validation CNAME per domain + account, and the
      // earlier CloudFront attempts already wrote these records.
      allowOverwrite: true,
    });
  });
  const validation = new aws.acm.CertificateValidation("AstralRedirectAlbCertValidation", {
    certificateArn: cert.arn,
    validationRecordFqdns: validationRecords.map((r) => r.fqdn),
  });

  // #{path} has no leading slash; ALB drops the "?" when #{query} is empty.
  const redirect = {
    type: "redirect",
    redirect: {
      protocol: "HTTPS",
      host: targetHost,
      port: "443",
      path: "/#{path}",
      query: "#{query}",
      statusCode: "HTTP_301",
    },
  };
  new aws.lb.Listener("AstralRedirectHttp", {
    loadBalancerArn: lb.arn,
    port: 80,
    protocol: "HTTP",
    defaultActions: [redirect],
  });
  new aws.lb.Listener("AstralRedirectHttps", {
    loadBalancerArn: lb.arn,
    port: 443,
    protocol: "HTTPS",
    sslPolicy: "ELBSecurityPolicy-TLS13-1-2-2021-06",
    certificateArn: validation.certificateArn,
    defaultActions: [redirect],
  });

  names.forEach((name, i) => {
    new aws.route53.Record(`AstralRedirectAlias${i}`, {
      zoneId: zone.zoneId,
      name,
      type: "A",
      aliases: [{ name: lb.dnsName, zoneId: lb.zoneId, evaluateTargetHealth: false }],
      allowOverwrite: true,
    });
  });

  return { url: `https://${names[0]}` };
}

const redirectCode = `
async function handler(event) {
  const qsObj = event.request.querystring || {};
  const qs = Object.keys(qsObj)
    .map((k) => {
      const q = qsObj[k];
      const vals = q.multiValue ? q.multiValue.map((m) => m.value) : [q.value];
      return vals.map((v) => (v === "" ? k : k + "=" + v)).join("&");
    })
    .join("&");
  return {
    statusCode: 301,
    statusDescription: "Moved Permanently",
    headers: {
      location: { value: "https://${targetHost}" + event.request.uri + (qs ? "?" + qs : "") },
      "cache-control": { value: "public, max-age=86400" },
    },
  };
}
`;

// AWS managed cache policy "CachingDisabled".
const CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad";

function createCloudFrontRedirect() {
  const fn = new aws.cloudfront.Function("AstralRedirectFunction", {
    runtime: "cloudfront-js-2.0",
    comment: "301 astral.us -> coy.bot",
    code: redirectCode,
    publish: true,
  });

  return new sst.aws.Cdn("AstralRedirect", {
    comment: "astral.us -> coy.bot redirect",
    domain: {
      name: names[0],
      aliases: names.slice(1),
      // The ACM validation CNAMEs are already in the zone.
      dns: sst.aws.dns({ override: true }),
    },
    // Never reached: the viewer-request function answers every request.
    origins: [
      {
        originId: "unused",
        domainName: targetHost,
        customOriginConfig: {
          httpPort: 80,
          httpsPort: 443,
          originProtocolPolicy: "https-only",
          originSslProtocols: ["TLSv1.2"],
        },
      },
    ],
    defaultCacheBehavior: {
      targetOriginId: "unused",
      viewerProtocolPolicy: "allow-all",
      allowedMethods: ["GET", "HEAD", "OPTIONS"],
      cachedMethods: ["GET", "HEAD"],
      cachePolicyId: CACHING_DISABLED,
      compress: true,
      functionAssociations: [{ eventType: "viewer-request", functionArn: fn.arn }],
    },
  });
}

export const astralRedirect =
  $app.stage !== "prod"
    ? undefined
    : VIA === "alb"
      ? createAlbRedirect()
      : createCloudFrontRedirect();
