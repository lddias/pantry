var dt = undefined;
var categories = new Set();
var locations = new Set();
var ws = new WebSocket("ws://" + location.host + "/pantry");

function serializeForm (arrayData) {
  var objectData;
  objectData = {};

  $.each(arrayData, function() {
    var value;

    if (this.value != null) {
      value = this.value;
    } else {
      value = '';
    }

    if (objectData[this.name] != null) {
      if (!objectData[this.name].push) {
        objectData[this.name] = [objectData[this.name]];
      }

      objectData[this.name].push(value);
    } else {
      objectData[this.name] = value;
    }
  });

  return objectData;
};

function addItem(e) {
    e.preventDefault();
    var x = $("#new-item").serializeArray();
    var y = serializeForm(x);
    console.log('sending:');
    console.log(y);
    y['quantity'] = Number(y['quantity']);
    ws.send(JSON.stringify(y));
    return false;
};

function init_tagsinput(selector, list) {
    var bloodhound = new Bloodhound({
        datumTokenizer: Bloodhound.tokenizers.whitespace,
        queryTokenizer: Bloodhound.tokenizers.whitespace,
        local: list
    });
    bloodhound.initialize();
    function searchWithDefaults(q, sync) {
      if (q === '') {
        sync(bloodhound.index.all());
      } else {
        bloodhound.search(q, sync);
      }
    }
    $(selector).tagsinput({
        tagClass: 'badge badge-primary',
        typeaheadjs: [{
                hint: true,
                highlight: true,
                minLength: 0
            },
            {
                source: searchWithDefaults
            }
        ]
    });
}

ws.onmessage = function(event) {
    console.log(event);
    var data = JSON.parse(event.data);
    $(document).ready(function() {
        categories = new Set();
        if (dt === undefined) {
            dt = $('#example').DataTable({
                responsive: true,
                data: data,
                columnDefs: [{
                        "render": function(data, type, row) {
                            if (type == "display")
                                return data.sort().map(function(d) {
                                    categories.add(d);
                                    return '<span class="badge badge-primary" style="margin: 1px">' + d + '</span>'
                                }).join('');
                            return data;
                        },
                        "targets": 2
                    },
                    {
                        "render": function(data, type, row) {
                            if (type == "display")
                                return data.sort().map(function(d) {
                                    locations.add(d);
                                    return '<span class="badge badge-primary" style="margin: 1px">' + d + '</span>'
                                }).join('');
                            return data;
                        },
                        "targets": 1
                    }
                ],
                initComplete: function(settings, json) {
                    $('#categories').tagsinput('destroy');
                    init_tagsinput('#categories', Array.from(categories));
                    $('#location').tagsinput('destroy');
                    init_tagsinput('#location', Array.from(locations));
                }
            });
        } else {
            dt.clear().draw();
            dt.rows.add(data).draw();
        }
    });
};
ws.onopen = function(event) {
    console.log("WebSocket is open now.");
    ws.send('request');
};
