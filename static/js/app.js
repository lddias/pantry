var dt = undefined;
var categories = new Set();
var locations = new Set();
var ws = new WebSocket("ws://" + location.host + "/pantry");

function serializeForm(arrayData) {
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

function addItem(event) {
    event.preventDefault();
    var form = event.target;
    if (form.checkValidity() === false) {
        event.stopPropagation();
    } else {
        var x = $("#new-item").serializeArray();
        var y = serializeForm(x);
        console.log('sending:');
        console.log(y);
        y['quantity'] = Number(y['quantity']);
        ws.send(JSON.stringify(y));
    }
    form.classList.add('was-validated');
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
    $(selector).tagsinput('input').blur(function() {
        $(selector).tagsinput('add', $(this).val());
        $(this).val('');
    });
}

function datatableUpdateCallback(settings, json) {
    $('#categories').tagsinput('destroy');
    init_tagsinput('#categories', Array.from(categories));
    $('#location').tagsinput('destroy');
    init_tagsinput('#location', Array.from(locations));
};

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
                        render: function(data, type, row) {
                            if (type == "display")
                                return data.sort().map(function(d) {
                                    categories.add(d);
                                    return '<span class="badge badge-primary" style="margin: 1px">' + d + '</span>'
                                }).join('');
                            return data;
                        },
                        targets: 3
                    },
                    {
                        render: function(data, type, row) {
                            if (type == "display")
                                return data.sort().map(function(d) {
                                    locations.add(d);
                                    return '<span class="badge badge-primary" style="margin: 1px">' + d + '</span>'
                                }).join('');
                            return data;
                        },
                        targets: 2
                    },
                    {
                        visible: false,
                        targets: 0
                    }
                ],
                initComplete: datatableUpdateCallback,
                drawCallback: datatableUpdateCallback
            });
        } else {
            dt.clear().draw();
            dt.rows.add(data).draw();
        }
        $('#example tbody').on('click', 'tr', function () {
            var data = dt.row( this ).data();
            $('#_id').val(data[0]);
            $('#name').val(data[1]);
            $('#location').tagsinput('removeAll');
            data[2].forEach(function (d) {
                $('#location').tagsinput('add', d);
            });
            $('#categories').tagsinput('removeAll');
            data[3].forEach(function (d) {
                $('#categories').tagsinput('add', d);
            });
            $('#quantity').val(data[4]);
            $('#expiration').val(data[5]);
            $('#collapseExample').collapse('show');
        } );
    });
};

ws.onopen = function(event) {
    console.log("WebSocket is open now.");
    ws.send('request');
};
