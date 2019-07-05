(function (__window) {
var exports = {};
Object.defineProperty(exports, '__esModule', { value: true });

/*! *****************************************************************************
Copyright (c) Microsoft Corporation. All rights reserved.
Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License. You may obtain a copy of the
License at http://www.apache.org/licenses/LICENSE-2.0

THIS CODE IS PROVIDED ON AN *AS IS* BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, EITHER EXPRESS OR IMPLIED, INCLUDING WITHOUT LIMITATION ANY IMPLIED
WARRANTIES OR CONDITIONS OF TITLE, FITNESS FOR A PARTICULAR PURPOSE,
MERCHANTABLITY OR NON-INFRINGEMENT.

See the Apache Version 2.0 License for specific language governing permissions
and limitations under the License.
***************************************************************************** */
/* global Reflect, Promise */

var extendStatics = function(d, b) {
    extendStatics = Object.setPrototypeOf ||
        ({ __proto__: [] } instanceof Array && function (d, b) { d.__proto__ = b; }) ||
        function (d, b) { for (var p in b) if (b.hasOwnProperty(p)) d[p] = b[p]; };
    return extendStatics(d, b);
};

function __extends(d, b) {
    extendStatics(d, b);
    function __() { this.constructor = d; }
    d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
}

var __assign = function() {
    __assign = Object.assign || function __assign(t) {
        for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
        }
        return t;
    };
    return __assign.apply(this, arguments);
};

function __read(o, n) {
    var m = typeof Symbol === "function" && o[Symbol.iterator];
    if (!m) return o;
    var i = m.call(o), r, ar = [], e;
    try {
        while ((n === void 0 || n-- > 0) && !(r = i.next()).done) ar.push(r.value);
    }
    catch (error) { e = { error: error }; }
    finally {
        try {
            if (r && !r.done && (m = i["return"])) m.call(i);
        }
        finally { if (e) throw e.error; }
    }
    return ar;
}

function __spread() {
    for (var ar = [], i = 0; i < arguments.length; i++)
        ar = ar.concat(__read(arguments[i]));
    return ar;
}

/** An error emitted by Sentry SDKs and related utilities. */
var SentryError = /** @class */ (function (_super) {
    __extends(SentryError, _super);
    function SentryError(message) {
        var _newTarget = this.constructor;
        var _this = _super.call(this, message) || this;
        _this.message = message;
        // tslint:disable:no-unsafe-any
        _this.name = _newTarget.prototype.constructor.name;
        Object.setPrototypeOf(_this, _newTarget.prototype);
        return _this;
    }
    return SentryError;
}(Error));

/**
 * Checks whether given value's type is one of a few Error or Error-like
 * {@link isError}.
 *
 * @param wat A value to be checked.
 * @returns A boolean representing the result.
 */
/**
 * Checks whether given value's type is an regexp
 * {@link isRegExp}.
 *
 * @param wat A value to be checked.
 * @returns A boolean representing the result.
 */
function isRegExp(wat) {
    return Object.prototype.toString.call(wat) === '[object RegExp]';
}

/**
 * Requires a module which is protected _against bundler minification.
 *
 * @param request The module path to resolve
 */
/**
 * Checks whether we're in the Node.js or Browser environment
 *
 * @returns Answer to given question
 */
function isNodeEnv() {
    // tslint:disable:strict-type-predicates
    return Object.prototype.toString.call(typeof process !== 'undefined' ? process : 0) === '[object process]';
}
var fallbackGlobalObject = {};
/**
 * Safely get global scope object
 *
 * @returns Global scope object
 */
function getGlobalObject() {
    return (isNodeEnv()
        ? global
        : typeof window !== 'undefined'
            ? window
            : typeof self !== 'undefined'
                ? self
                : fallbackGlobalObject);
}
/** JSDoc */
function consoleSandbox(callback) {
    var global = getGlobalObject();
    var levels = ['debug', 'info', 'warn', 'error', 'log', 'assert'];
    if (!('console' in global)) {
        return callback();
    }
    var originalConsole = global.console;
    var wrappedLevels = {};
    // Restore all wrapped console methods
    levels.forEach(function (level) {
        if (level in global.console && originalConsole[level].__sentry__) {
            wrappedLevels[level] = originalConsole[level].__sentry_wrapped__;
            originalConsole[level] = originalConsole[level].__sentry_original__;
        }
    });
    // Perform callback manipulations
    var result = callback();
    // Revert restoration to wrapped state
    Object.keys(wrappedLevels).forEach(function (level) {
        originalConsole[level] = wrappedLevels[level];
    });
    return result;
}

// TODO: Implement different loggers for different environments
var global$1 = getGlobalObject();
/** Prefix for logging strings */
var PREFIX = 'Sentry Logger ';
/** JSDoc */
var Logger = /** @class */ (function () {
    /** JSDoc */
    function Logger() {
        this._enabled = false;
    }
    /** JSDoc */
    Logger.prototype.disable = function () {
        this._enabled = false;
    };
    /** JSDoc */
    Logger.prototype.enable = function () {
        this._enabled = true;
    };
    /** JSDoc */
    Logger.prototype.log = function () {
        var args = [];
        for (var _i = 0; _i < arguments.length; _i++) {
            args[_i] = arguments[_i];
        }
        if (!this._enabled) {
            return;
        }
        consoleSandbox(function () {
            global$1.console.log(PREFIX + "[Log]: " + args.join(' ')); // tslint:disable-line:no-console
        });
    };
    /** JSDoc */
    Logger.prototype.warn = function () {
        var args = [];
        for (var _i = 0; _i < arguments.length; _i++) {
            args[_i] = arguments[_i];
        }
        if (!this._enabled) {
            return;
        }
        consoleSandbox(function () {
            global$1.console.warn(PREFIX + "[Warn]: " + args.join(' ')); // tslint:disable-line:no-console
        });
    };
    /** JSDoc */
    Logger.prototype.error = function () {
        var args = [];
        for (var _i = 0; _i < arguments.length; _i++) {
            args[_i] = arguments[_i];
        }
        if (!this._enabled) {
            return;
        }
        consoleSandbox(function () {
            global$1.console.error(PREFIX + "[Error]: " + args.join(' ')); // tslint:disable-line:no-console
        });
    };
    return Logger;
}());
// Ensure we only have a single logger instance, even if multiple versions of @sentry/utils are being used
global$1.__SENTRY__ = global$1.__SENTRY__ || {};
var logger = global$1.__SENTRY__.logger || (global$1.__SENTRY__.logger = new Logger());

// tslint:disable:no-unsafe-any

/**
 * Wrap a given object method with a higher-order function
 *
 * @param source An object that contains a method to be wrapped.
 * @param name A name of method to be wrapped.
 * @param replacement A function that should be used to wrap a given method.
 * @returns void
 */
function fill(source, name, replacement) {
    if (!(name in source)) {
        return;
    }
    var original = source[name];
    var wrapped = replacement(original);
    // Make sure it's a function first, as we need to attach an empty prototype for `defineProperties` to work
    // otherwise it'll throw "TypeError: Object.defineProperties called on non-object"
    // tslint:disable-next-line:strict-type-predicates
    if (typeof wrapped === 'function') {
        try {
            wrapped.prototype = wrapped.prototype || {};
            Object.defineProperties(wrapped, {
                __sentry__: {
                    enumerable: false,
                    value: true,
                },
                __sentry_original__: {
                    enumerable: false,
                    value: original,
                },
                __sentry_wrapped__: {
                    enumerable: false,
                    value: wrapped,
                },
            });
        }
        catch (_Oo) {
            // This can throw if multiple fill happens on a global object like XMLHttpRequest
            // Fixes https://github.com/getsentry/sentry-javascript/issues/2043
        }
    }
    source[name] = wrapped;
}

// Slightly modified (no IE8 support, ES6) and transcribed to TypeScript

/**
 * Checks if the value matches a regex or includes the string
 * @param value The string value to be checked against
 * @param pattern Either a regex or a string that must be contained in value
 */
function isMatchingPattern(value, pattern) {
    if (isRegExp(pattern)) {
        return pattern.test(value);
    }
    if (typeof pattern === 'string') {
        return value.includes(pattern);
    }
    return false;
}

/**
 * Tells whether current environment supports Fetch API
 * {@link supportsFetch}.
 *
 * @returns Answer to the given question.
 */
function supportsFetch() {
    if (!('fetch' in getGlobalObject())) {
        return false;
    }
    try {
        // tslint:disable-next-line:no-unused-expression
        new Headers();
        // tslint:disable-next-line:no-unused-expression
        new Request('');
        // tslint:disable-next-line:no-unused-expression
        new Response();
        return true;
    }
    catch (e) {
        return false;
    }
}
/**
 * Tells whether current environment supports Fetch API natively
 * {@link supportsNativeFetch}.
 *
 * @returns Answer to the given question.
 */
function supportsNativeFetch() {
    if (!supportsFetch()) {
        return false;
    }
    var global = getGlobalObject();
    return global.fetch.toString().indexOf('native') !== -1;
}

/** SyncPromise internal states */
var States;
(function (States) {
    /** Pending */
    States["PENDING"] = "PENDING";
    /** Resolved / OK */
    States["RESOLVED"] = "RESOLVED";
    /** Rejected / Error */
    States["REJECTED"] = "REJECTED";
})(States || (States = {}));

/**
 * Tracing Integration
 */
var Tracing = /** @class */ (function () {
    /**
     * Constructor for Tracing
     *
     * @param _options TracingOptions
     */
    function Tracing(_options) {
        if (_options === void 0) { _options = {}; }
        this._options = _options;
        /**
         * @inheritDoc
         */
        this.name = Tracing.id;
        if (!Array.isArray(_options.tracingOrigins) || _options.tracingOrigins.length === 0) {
            consoleSandbox(function () {
                var defaultTracingOrigins = ['localhost', /^\//];
                // @ts-ignore
                console.warn('Sentry: You need to define `tracingOrigins` in the options. Set an array of urls or patterns to trace.');
                // @ts-ignore
                console.warn("Sentry: We added a reasonable default for you: " + defaultTracingOrigins);
                _options.tracingOrigins = defaultTracingOrigins;
            });
        }
    }
    /**
     * @inheritDoc
     */
    Tracing.prototype.setupOnce = function (_, getCurrentHub) {
        if (this._options.traceXHR !== false) {
            this._traceXHR(getCurrentHub);
        }
        if (this._options.traceFetch !== false) {
            this._traceFetch(getCurrentHub);
        }
        if (this._options.autoStartOnDomReady !== false) {
            getGlobalObject().addEventListener('DOMContentLoaded', function () {
                Tracing.startTrace(getCurrentHub(), getGlobalObject().location.href);
            });
            getGlobalObject().document.onreadystatechange = function () {
                if (document.readyState === 'complete') {
                    Tracing.startTrace(getCurrentHub(), getGlobalObject().location.href);
                }
            };
        }
    };
    /**
     * Starts a new trace
     * @param hub The hub to start the trace on
     * @param transaction Optional transaction
     */
    Tracing.startTrace = function (hub, transaction) {
        hub.configureScope(function (scope) {
            scope.startSpan();
            scope.setTransaction(transaction);
        });
    };
    /**
     * JSDoc
     */
    Tracing.prototype._traceXHR = function (getCurrentHub) {
        if (!('XMLHttpRequest' in getGlobalObject())) {
            return;
        }
        var xhrproto = XMLHttpRequest.prototype;
        fill(xhrproto, 'open', function (originalOpen) {
            return function () {
                var args = [];
                for (var _i = 0; _i < arguments.length; _i++) {
                    args[_i] = arguments[_i];
                }
                // @ts-ignore
                var self = getCurrentHub().getIntegration(Tracing);
                if (self) {
                    self._xhrUrl = args[1];
                }
                // tslint:disable-next-line: no-unsafe-any
                return originalOpen.apply(this, args);
            };
        });
        fill(xhrproto, 'send', function (originalSend) {
            return function () {
                var _this = this;
                var args = [];
                for (var _i = 0; _i < arguments.length; _i++) {
                    args[_i] = arguments[_i];
                }
                // @ts-ignore
                var self = getCurrentHub().getIntegration(Tracing);
                if (self && self._xhrUrl && self._options.tracingOrigins) {
                    var url_1 = self._xhrUrl;
                    var headers_1 = getCurrentHub().traceHeaders();
                    // tslint:disable-next-line: prefer-for-of
                    var isWhitelisted = self._options.tracingOrigins.some(function (origin) {
                        return isMatchingPattern(url_1, origin);
                    });
                    if (isWhitelisted && this.setRequestHeader) {
                        Object.keys(headers_1).forEach(function (key) {
                            _this.setRequestHeader(key, headers_1[key]);
                        });
                    }
                }
                // tslint:disable-next-line: no-unsafe-any
                return originalSend.apply(this, args);
            };
        });
    };
    /**
     * JSDoc
     */
    Tracing.prototype._traceFetch = function (getCurrentHub) {
        if (!supportsNativeFetch()) {
            return;
        }

        console.log("PATCHING FETCH");

        // tslint:disable: only-arrow-functions
        fill(getGlobalObject(), 'fetch', function (originalFetch) {
            return function () {
                var args = [];
                for (var _i = 0; _i < arguments.length; _i++) {
                    args[_i] = arguments[_i];
                }
                // @ts-ignore
                var self = getCurrentHub().getIntegration(Tracing);
                if (self && self._options.tracingOrigins) {
                    console.log("blafalseq");
                    var url_2 = args[0];
                    var options = args[1] = args[1] || {};
                    var whiteListed_1 = false;
                    self._options.tracingOrigins.forEach(function (whiteListUrl) {
                        if (!whiteListed_1) {
                            whiteListed_1 = isMatchingPattern(url_2, whiteListUrl);
                            console.log('a', url_2, whiteListUrl);
                        }
                    });
                    if (whiteListed_1) {
                        console.log('aaaaaa', options, whiteListed_1);
                        if (options.headers) {

                            if (Array.isArray(options.headers)) {
                                options.headers = __spread(options.headers, Object.entries(getCurrentHub().traceHeaders()));
                            }
                            else {
                                options.headers = __assign({}, options.headers, getCurrentHub().traceHeaders());
                            }
                        }
                        else {
                            options.headers = getCurrentHub().traceHeaders();
                        }

                        console.log(options.headers);
                    }
                }

                args[1] = options;
                // tslint:disable-next-line: no-unsafe-any
                return originalFetch.apply(getGlobalObject(), args);
            };
        });
        // tslint:enable: only-arrow-functions
    };
    /**
     * @inheritDoc
     */
    Tracing.id = 'Tracing';
    return Tracing;
}());

exports.Tracing = Tracing;


  __window.Sentry = __window.Sentry || {};
  __window.Sentry.Integrations = __window.Sentry.Integrations || {};
  Object.assign(__window.Sentry.Integrations, exports);












}(window));
//# sourceMappingURL=tracing.js.map
