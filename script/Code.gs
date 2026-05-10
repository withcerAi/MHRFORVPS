const AUTH_KEY = "CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY";
const VPS_IP = "YOUR_VPS_IP_HERE";
const WORKER_URL = "http://" + VPS_IP + ":8081";

// Or, if you prefer a full URL instead of VPS_IP:
// const WORKER_URL = "http://YOUR_VPS_IP_HERE:8081";

const SKIP_HEADERS = {
  host: 1,
  connection: 1,
  "content-length": 1,
  "transfer-encoding": 1,
  "proxy-connection": 1,
  "proxy-authorization": 1
};

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);

    if (req.k !== AUTH_KEY) {
      return _json({ e: "unauthorized" });
    }

    if (Array.isArray(req.q)) {
      return _doBatch(req.q);
    }

    return _doSingle(req);

  } catch (err) {
    return _json({ e: String(err) });
  }
}

function _doSingle(req) {
  if (!req.u || typeof req.u !== "string" || !req.u.match(/^https?:\/\//i)) {
    return _json({ e: "bad url" });
  }

  var payload = _buildWorkerPayload(req);

  var resp = UrlFetchApp.fetch(WORKER_URL, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    followRedirects: true
  });

  try {
    return _json(JSON.parse(resp.getContentText()));
  } catch (e) {
    return _json({
      e: "invalid worker response",
      raw: resp.getContentText()
    });
  }
}

function _doBatch(items) {
  var fetchArgs = [];
  var errorMap = {};

  for (var i = 0; i < items.length; i++) {
    var item = items[i];

    if (!item.u || typeof item.u !== "string" || !item.u.match(/^https?:\/\//i)) {
      errorMap[i] = "bad url";
      continue;
    }

    var payload = _buildWorkerPayload(item);

    fetchArgs.push({
      _i: i,
      _o: {
        url: WORKER_URL,
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
        followRedirects: true
      }
    });
  }

  var responses = [];

  if (fetchArgs.length > 0) {
    responses = UrlFetchApp.fetchAll(fetchArgs.map(function(x) {
      return x._o;
    }));
  }

  var results = [];
  var rIdx = 0;

  for (var j = 0; j < items.length; j++) {
    if (errorMap.hasOwnProperty(j)) {
      results.push({ e: errorMap[j] });
    } else {
      var resp = responses[rIdx++];

      try {
        results.push(JSON.parse(resp.getContentText()));
      } catch (e) {
        results.push({
          e: "invalid worker response",
          raw: resp.getContentText()
        });
      }
    }
  }

  return _json({ q: results });
}

function _buildWorkerPayload(req) {
  var headers = {};

  if (req.h && typeof req.h === "object") {
    for (var k in req.h) {
      if (req.h.hasOwnProperty(k) && !SKIP_HEADERS[k.toLowerCase()]) {
        headers[k] = req.h[k];
      }
    }
  }

  return {
    k: AUTH_KEY,
    u: req.u,
    m: (req.m || "GET").toUpperCase(),
    h: headers,
    b: req.b || null,
    ct: req.ct || null,
    r: req.r !== false
  };
}

function doGet(e) {
  return HtmlService.createHtmlOutput(
    "<!DOCTYPE html><html><head><title>MHR Relay</title></head>" +
    '<body style="font-family:sans-serif;max-width:600px;margin:40px auto">' +
    "<h1>Relay Active</h1><p>VPS relay routing enabled.</p>" +
    "</body></html>"
  );
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}