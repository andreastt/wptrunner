/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

//var callback = arguments[arguments.length - 1];
window.sessionId = "%(session_id)s";
window.timeout_multiplier = %(timeout_multiplier)d;
window.result = {};

window.done = function(tests, status) {
  //clearTimeout(timer);
  var test_results = tests.map(function(x) {
    return {name:x.name, status:x.status, message:x.message}
  });
  window.result = {test: "%(url)s",
    tests: test_results,
    status: status.status,
    message:status.message};
}

window.setTimeout(function() { window.win = window.open("%(abs_url)s", "%(window_id)s") }, 0);

/*
var timer = setTimeout(function() {
  window.win.timeout();
  window.win.close();
}, %(timeout)s);
*/